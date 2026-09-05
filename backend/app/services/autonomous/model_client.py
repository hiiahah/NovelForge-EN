"""Role-based model access for the autonomous pipeline.

Every role has its own temperature, token budget, retry count and prompt
version; they may all resolve to the same Kimi K3 configuration. Every call is
persisted as a ``ModelInvocation`` (role, prompt version, schema, usage,
latency, retries, validation status) so runs are auditable.

The client is an interface (``ModelClient``) so tests inject deterministic
fakes; ``LLMModelClient`` is the production implementation on top of
``llm_service`` / ``chat_model_factory``. The client never guarantees identical
prose across reruns: it guarantees recorded inputs, versions and outcomes.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Type, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from app.db.models import LLMConfig, ModelInvocation
from app.services.autonomous import failures as fail

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class RolePolicy:
    role: str
    temperature: float
    max_tokens: int
    timeout: float
    max_retries: int


ROLE_POLICIES: Dict[str, RolePolicy] = {
    "source_extractor": RolePolicy("source_extractor", 0.2, 12000, 300, 3),
    "source_analyst": RolePolicy("source_analyst", 0.3, 16000, 300, 3),
    "fingerprint_synthesizer": RolePolicy("fingerprint_synthesizer", 0.3, 12000, 300, 3),
    "storyline_ideator": RolePolicy("storyline_ideator", 0.9, 16000, 420, 3),
    "originality_critic": RolePolicy("originality_critic", 0.2, 8000, 240, 2),
    "novel_architect": RolePolicy("novel_architect", 0.5, 20000, 600, 3),
    "chapter_planner": RolePolicy("chapter_planner", 0.5, 16000, 480, 3),
    "drafter": RolePolicy("drafter", 0.8, 12000, 480, 2),
    "claim_extractor": RolePolicy("claim_extractor", 0.1, 6000, 180, 2),
    "continuity_validator": RolePolicy("continuity_validator", 0.1, 6000, 180, 2),
    "style_evaluator": RolePolicy("style_evaluator", 0.2, 4000, 180, 2),
    "repair_editor": RolePolicy("repair_editor", 0.4, 12000, 480, 2),
    "whole_novel_editor": RolePolicy("whole_novel_editor", 0.4, 16000, 600, 2),
}

# The legacy Forge pipeline roles map onto autonomous roles.
FORGE_ROLE_MAP = {"drafting": "drafter", "repair": "repair_editor", "analysis": "source_analyst", "planning": "chapter_planner", "validator": "continuity_validator", "evaluator": "style_evaluator"}


class ModelClient(Protocol):
    async def structured(self, *, role: str, schema: Type[T], system_prompt: str, user_prompt: str, prompt_version: str, stage: str = "") -> T: ...

    async def text(self, *, role: str, system_prompt: str, user_prompt: str, prompt_version: str, stage: str = "") -> str: ...


def _estimate_tokens(*texts: str) -> int:
    from app.services.ai.core.token_utils import estimate_tokens

    return sum(estimate_tokens(t or "") for t in texts)


class InvocationRecorder:
    """Persists ``ModelInvocation`` rows and aggregates usage onto the job."""

    def __init__(self, session: Session, *, job_id: Optional[int] = None, project_id: Optional[int] = None):
        self.session = session
        self.job_id = job_id
        self.project_id = project_id
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def record(self, **fields: Any) -> ModelInvocation:
        row = ModelInvocation(job_id=self.job_id, project_id=self.project_id, **fields)
        self.session.add(row)
        self.calls += 1
        self.input_tokens += int(fields.get("input_tokens") or fields.get("input_tokens_estimate") or 0)
        self.output_tokens += int(fields.get("output_tokens") or 0)
        return row


class LLMModelClient:
    """Production client: resolves a role to an LLM config and calls the model."""

    def __init__(self, session: Session, *, default_llm_config_id: int, role_llm_config_ids: Optional[Dict[str, int]] = None, fallback_llm_config_id: Optional[int] = None, recorder: Optional[InvocationRecorder] = None):
        self.session = session
        self.default_llm_config_id = int(default_llm_config_id)
        self.role_llm_config_ids = {k: int(v) for k, v in (role_llm_config_ids or {}).items() if v}
        self.fallback_llm_config_id = int(fallback_llm_config_id) if fallback_llm_config_id else None
        self.recorder = recorder or InvocationRecorder(session)

    def config_for(self, role: str, *, fallback: bool = False) -> int:
        if fallback and self.fallback_llm_config_id:
            return self.fallback_llm_config_id
        return self.role_llm_config_ids.get(role) or self.default_llm_config_id

    def _model_name(self, cid: int) -> str:
        cfg = self.session.get(LLMConfig, cid)
        return cfg.model_name if cfg else ""

    async def structured(self, *, role: str, schema: Type[T], system_prompt: str, user_prompt: str, prompt_version: str, stage: str = "") -> T:
        from app.services.ai.core.llm_service import generate_structured

        policy = ROLE_POLICIES.get(role) or ROLE_POLICIES["source_analyst"]
        cid = self.config_for(role)
        started = time.monotonic()
        prompt = user_prompt
        last_exc: Optional[BaseException] = None
        for attempt in range(1, policy.max_retries + 2):
            try:
                result = await generate_structured(self.session, cid, prompt, schema, system_prompt=system_prompt, max_tokens=policy.max_tokens, max_retries=1, temperature=policy.temperature, timeout=policy.timeout, track_stats=True)
                if not isinstance(result, schema):
                    result = schema.model_validate(result.model_dump() if hasattr(result, "model_dump") else result)
                self.recorder.record(stage=stage, role=role, llm_config_id=cid, model_name=self._model_name(cid), prompt_version=prompt_version, schema_name=schema.__name__, temperature=policy.temperature, input_tokens_estimate=_estimate_tokens(system_prompt, prompt), output_tokens=_estimate_tokens(json.dumps(result.model_dump(mode="json"), ensure_ascii=False)), latency_ms=int((time.monotonic() - started) * 1000), retries=attempt - 1, validation_status="ok")
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                last_exc = exc
                category = fail.classify_exception(exc)
                logger.warning(f"[Autonomous] {role} structured call failed (attempt {attempt}, {category}): {exc}")
                if category == fail.MALFORMED_OUTPUT and attempt == 2:
                    # Rung 2 of the ladder: clarified schema instructions.
                    prompt = user_prompt + "\n\n[FORMAT REPAIR]\nYour previous answer did not match the required JSON schema. Return ONLY a single JSON object that validates against the schema. No prose, no markdown fences, no comments."
                if category == fail.PROVIDER_FAILURE and self.fallback_llm_config_id and attempt >= 2:
                    cid = self.fallback_llm_config_id
                if attempt > policy.max_retries:
                    break
                await asyncio.sleep(min(2 ** (attempt - 1), 8) if category == fail.PROVIDER_FAILURE else 0)
        self.recorder.record(stage=stage, role=role, llm_config_id=cid, model_name=self._model_name(cid), prompt_version=prompt_version, schema_name=schema.__name__, temperature=policy.temperature, input_tokens_estimate=_estimate_tokens(system_prompt, user_prompt), latency_ms=int((time.monotonic() - started) * 1000), retries=policy.max_retries, validation_status="error", error=str(last_exc)[:500])
        raise fail.StageFailure(fail.classify_exception(last_exc or RuntimeError("unknown")), f"{role} structured call failed: {last_exc}")

    async def text(self, *, role: str, system_prompt: str, user_prompt: str, prompt_version: str, stage: str = "") -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.services.ai.core.chat_model_factory import build_chat_model

        policy = ROLE_POLICIES.get(role) or ROLE_POLICIES["drafter"]
        cid = self.config_for(role)
        started = time.monotonic()
        last_exc: Optional[BaseException] = None
        for attempt in range(1, policy.max_retries + 2):
            try:
                model = build_chat_model(session=self.session, llm_config_id=cid, temperature=policy.temperature, max_tokens=policy.max_tokens, timeout=policy.timeout)
                result = await model.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
                content = getattr(result, "content", result)
                if isinstance(content, list):
                    content = "".join(str(c.get("text", "") if isinstance(c, dict) else c) for c in content)
                text = str(content)
                if not text.strip():
                    raise ValueError("LLM returned an empty response")
                usage = getattr(result, "usage_metadata", None) or {}
                self.recorder.record(stage=stage, role=role, llm_config_id=cid, model_name=self._model_name(cid), prompt_version=prompt_version, schema_name="text", temperature=policy.temperature, input_tokens_estimate=_estimate_tokens(system_prompt, user_prompt), input_tokens=int(usage.get("input_tokens") or 0) if isinstance(usage, dict) else 0, output_tokens=int(usage.get("output_tokens") or 0) if isinstance(usage, dict) else _estimate_tokens(text), latency_ms=int((time.monotonic() - started) * 1000), retries=attempt - 1, validation_status="ok")
                return text
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                category = fail.classify_exception(exc)
                logger.warning(f"[Autonomous] {role} text call failed (attempt {attempt}, {category}): {exc}")
                if category == fail.PROVIDER_FAILURE and self.fallback_llm_config_id and attempt >= 2:
                    cid = self.fallback_llm_config_id
                if attempt > policy.max_retries:
                    break
                await asyncio.sleep(min(2 ** (attempt - 1), 8) if category == fail.PROVIDER_FAILURE else 0)
        self.recorder.record(stage=stage, role=role, llm_config_id=cid, model_name=self._model_name(cid), prompt_version=prompt_version, schema_name="text", temperature=policy.temperature, input_tokens_estimate=_estimate_tokens(system_prompt, user_prompt), latency_ms=int((time.monotonic() - started) * 1000), retries=policy.max_retries, validation_status="error", error=str(last_exc)[:500])
        raise fail.StageFailure(fail.classify_exception(last_exc or RuntimeError("unknown")), f"{role} text call failed: {last_exc}")


class ForgeDrafterAdapter:
    """Adapts a ``ModelClient`` to the Forge ``Drafter`` protocol used by ``pipeline.run_chapter``."""

    def __init__(self, client: ModelClient, *, stage: str = "CHAPTER_GENERATION_LOOP"):
        self.client = client
        self.stage = stage

    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context: Any) -> str:
        from app.services.forge.pipeline import DRAFT_PROMPT_VERSION, REPAIR_PROMPT_VERSION

        auto_role = FORGE_ROLE_MAP.get(role, role)
        return await self.client.text(role=auto_role, system_prompt=system_prompt, user_prompt=user_prompt, prompt_version=DRAFT_PROMPT_VERSION if role == "drafting" else REPAIR_PROMPT_VERSION, stage=f"{self.stage}:ch{getattr(context, 'chapter_number', '?')}")


def validate_or_raise(schema: Type[T], data: Any) -> T:
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise fail.StageFailure(fail.MALFORMED_OUTPUT, f"{schema.__name__} validation failed: {exc.errors()[:3]}")


__all__ = ["FORGE_ROLE_MAP", "ForgeDrafterAdapter", "InvocationRecorder", "LLMModelClient", "ModelClient", "ROLE_POLICIES", "RolePolicy", "validate_or_raise"]
