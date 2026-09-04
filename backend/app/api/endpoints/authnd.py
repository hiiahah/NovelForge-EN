"""AuthND (NVIDIA Build browser-backed route) API endpoints.

Provides:
- OpenAI-compatible chat completion endpoint (/api/authnd/v1/chat/completions)
- Direct prediction endpoint (/api/authnd/predict)
- Model listing endpoint (/api/authnd/models)
- Status endpoint (/api/authnd/status)
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas.response import ApiResponse
from app.services.ai.providers import authnd_auth


router = APIRouter()


class ChatMessagePayload(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model: str = Field(default=authnd_auth.DEFAULT_MODEL, description="Model path, e.g. moonshotai/kimi-k3")
    messages: List[ChatMessagePayload]
    temperature: Optional[float] = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=32768, ge=1)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stream: bool = False
    timeout: Optional[int] = None
    connect_timeout: Optional[float] = None
    proxy: Optional[str] = None
    reasoning_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None


class DirectPredictRequest(BaseModel):
    model: str = Field(default=authnd_auth.DEFAULT_MODEL)
    prompt: str
    system: Optional[str] = None
    temperature: Optional[float] = 0.3
    max_tokens: Optional[int] = 32768
    top_p: Optional[float] = None
    stream: bool = False
    timeout: Optional[int] = None
    proxy: Optional[str] = None
    reasoning_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None


@router.get("/models", response_model=ApiResponse[List[Dict[str, Any]]], summary="List available AuthND preset models")
def get_authnd_models():
    """Return a list of curated models available via NVIDIA Build AuthND."""
    models_data = [
        {"id": m, "name": m, "publisher": m.split("/")[0] if "/" in m else "nvidia", "provider": "authnd"}
        for m in authnd_auth.AUTHND_PRESET_MODELS
    ]
    return ApiResponse(data=models_data)


def compute_authnd_status(*, probe_network: bool = True) -> Dict[str, Any]:
    """Truthful readiness: ready | degraded | unavailable | missing_dependency | token_helper_unavailable."""
    helper = authnd_auth.token_helper_status()
    checks: Dict[str, Any] = {"token_helper": helper}
    status = "ready"
    if not helper.get("available"):
        status = "missing_dependency" if "PySide6" in str(helper.get("detail", "")) else "token_helper_unavailable"
    if probe_network and status == "ready":
        try:
            import requests as _requests

            resp = _requests.get(authnd_auth.BUILD_BASE_URL, timeout=8, allow_redirects=True, stream=True)
            resp.close()
            checks["build_reachable"] = 200 <= resp.status_code < 400
            if not checks["build_reachable"]:
                status = "degraded"
        except Exception as exc:
            checks["build_reachable"] = False
            checks["network_error"] = str(exc)[:200]
            status = "unavailable"
    return {
        "status": status,
        "provider": "authnd",
        "base_url": authnd_auth.BUILD_BASE_URL,
        "api_url": authnd_auth.API_BASE_URL,
        "predict_api_url": authnd_auth.PREDICT_API_BASE_URL,
        "default_model": authnd_auth.DEFAULT_MODEL,
        "models_count": len(authnd_auth.AUTHND_PRESET_MODELS),
        "preset_models": authnd_auth.AUTHND_PRESET_MODELS,
        "checks": checks,
    }


@router.get("/status", response_model=ApiResponse[Dict[str, Any]], summary="AuthND service status")
def get_authnd_status(probe: bool = Query(default=True, description="Also probe network reachability of build.nvidia.com")):
    """Report real AuthND readiness (dependencies, token helper, network)."""
    return ApiResponse(data=compute_authnd_status(probe_network=probe))


@router.post("/predict", summary="Direct prompt prediction via AuthND")
async def direct_predict(payload: DirectPredictRequest, request: Request):
    """Simple prediction endpoint taking a prompt and optional system instructions."""
    messages = []
    if payload.system:
        messages.append({"role": "system", "content": payload.system})
    messages.append({"role": "user", "content": payload.prompt})

    req = ChatCompletionRequest(
        model=payload.model,
        messages=[ChatMessagePayload(**m) for m in messages],
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        top_p=payload.top_p,
        stream=payload.stream,
        timeout=payload.timeout,
        proxy=payload.proxy,
        reasoning_enabled=payload.reasoning_enabled,
        reasoning_effort=payload.reasoning_effort,
    )
    return await chat_completions(req, request)


@router.post("/v1/chat/completions", summary="OpenAI-compatible Chat Completion endpoint for AuthND")
async def chat_completions(payload: ChatCompletionRequest, request: Request):
    """OpenAI-compatible chat completions route supporting both JSON and SSE streaming."""
    dict_messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    control = authnd_auth.RequestControl(label=completion_id)

    if not payload.stream:
        task = asyncio.get_running_loop().run_in_executor(
            None,
            lambda: authnd_auth.send_chat_completion(
                messages=dict_messages,
                model=payload.model,
                temperature=payload.temperature if payload.temperature is not None else 0.3,
                max_tokens=payload.max_tokens if payload.max_tokens is not None else 32768,
                top_p=payload.top_p,
                frequency_penalty=payload.frequency_penalty,
                presence_penalty=payload.presence_penalty,
                timeout=payload.timeout,
                connect_timeout=payload.connect_timeout,
                proxy=payload.proxy,
                reasoning_enabled=payload.reasoning_enabled,
                reasoning_effort=payload.reasoning_effort,
                stream=False,
                control=control,
            ),
        )
        try:
            result = await task
        except asyncio.CancelledError:
            control.cancel()
            raise
        except authnd_auth.AuthNDDependencyError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            status_code = 499 if "cancelled" in str(exc).lower() else 502
            raise HTTPException(status_code=status_code, detail=f"AuthND completion failed: {exc}")

        content = result.get("content") or ""
        reasoning = result.get("reasoning_content")
        finish_reason = result.get("finish_reason") or "stop"
        usage = result.get("usage") or {
            "prompt_tokens": 0,
            "completion_tokens": len(content) // 4,
            "total_tokens": len(content) // 4,
        }

        msg_dict: Dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning:
            msg_dict["reasoning_content"] = reasoning

        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": payload.model,
            "choices": [
                {
                    "index": 0,
                    "message": msg_dict,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage,
        }

    # Streaming mode via SSE. Exactly one terminal outcome is emitted:
    # a finish_reason chunk ("stop"/"length") on success, or an error chunk on
    # failure/cancellation, always followed by "data: [DONE]".
    async def sse_generator():
        chunk_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        outcome: Dict[str, Any] = {}

        def _on_chunk(delta_text: str, delta_reasoning: Optional[str] = None):
            loop.call_soon_threadsafe(chunk_queue.put_nowait, ("chunk", delta_text, delta_reasoning))

        def _run_stream():
            try:
                result = authnd_auth.send_chat_completion(
                    messages=dict_messages,
                    model=payload.model,
                    temperature=payload.temperature if payload.temperature is not None else 0.3,
                    max_tokens=payload.max_tokens if payload.max_tokens is not None else 32768,
                    top_p=payload.top_p,
                    frequency_penalty=payload.frequency_penalty,
                    presence_penalty=payload.presence_penalty,
                    timeout=payload.timeout,
                    connect_timeout=payload.connect_timeout,
                    proxy=payload.proxy,
                    reasoning_enabled=payload.reasoning_enabled,
                    reasoning_effort=payload.reasoning_effort,
                    stream=True,
                    chunk_callback=_on_chunk,
                    control=control,
                )
                loop.call_soon_threadsafe(chunk_queue.put_nowait, ("done", result, None))
            except Exception as e:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, ("error", str(e), None))

        worker = threading.Thread(target=_run_stream, daemon=True, name=f"authnd-sse-{completion_id}")
        worker.start()

        def _chunk(delta: Dict[str, Any], finish_reason: Optional[str]) -> str:
            body = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": payload.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

        yield _chunk({"role": "assistant"}, None)
        try:
            while True:
                if request is not None:
                    try:
                        disconnected = await request.is_disconnected()
                    except Exception:
                        disconnected = False
                    if disconnected:
                        control.cancel()
                        outcome["type"] = "cancelled"
                        break
                try:
                    kind, a, b = await asyncio.wait_for(chunk_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                if kind == "chunk":
                    delta: Dict[str, Any] = {}
                    if a:
                        delta["content"] = a
                    if b:
                        delta["reasoning_content"] = b
                    if delta:
                        yield _chunk(delta, None)
                elif kind == "done":
                    outcome["type"] = "done"
                    outcome["result"] = a
                    break
                else:
                    outcome["type"] = "error"
                    outcome["error"] = a
                    break
        except (asyncio.CancelledError, GeneratorExit):
            control.cancel()
            raise
        finally:
            if outcome.get("type") != "done":
                control.cancel()

        if outcome.get("type") == "done":
            result = outcome.get("result") or {}
            finish = result.get("finish_reason") or "stop"
            yield _chunk({}, finish if finish in ("stop", "length", "content_filter") else "stop")
        elif outcome.get("type") == "cancelled":
            yield _chunk({"content": "\n[Error: request cancelled by client]"}, "error")
        else:
            yield _chunk({"content": f"\n[Error: {outcome.get('error')}]"}, "error")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")
