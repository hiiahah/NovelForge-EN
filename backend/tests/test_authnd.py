"""AuthND provider tests. No browser, no network: token minting and the NVIDIA
request are monkeypatched at the module boundary."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel, Field

from app.services.ai.providers import authnd_auth as a
from app.services.ai.providers.chat_authnd import ChatAuthND, StructuredOutputError, parse_structured_text


# ------------------------------------------------------------ normalization
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("moonshotai/kimi-k3", ("moonshotai", "kimi-k3", "https://build.nvidia.com/moonshotai/kimi-k3")),
        ("authnd/moonshotai/kimi-k3", ("moonshotai", "kimi-k3", "https://build.nvidia.com/moonshotai/kimi-k3")),
        ("AUTHND/moonshotai/kimi-k3", ("moonshotai", "kimi-k3", "https://build.nvidia.com/moonshotai/kimi-k3")),
        ("kimi-k3", ("moonshotai", "kimi-k3", "https://build.nvidia.com/moonshotai/kimi-k3")),
        ("", ("moonshotai", "kimi-k3", "https://build.nvidia.com/moonshotai/kimi-k3")),
        ("   ", ("moonshotai", "kimi-k3", "https://build.nvidia.com/moonshotai/kimi-k3")),
        ("deepseek-ai/deepseek-v3.1", ("deepseek-ai", "deepseek-v3.1", "https://build.nvidia.com/deepseek-ai/deepseek-v3_1")),
    ],
)
def test_normalize_model(raw, expected, monkeypatch):
    monkeypatch.delenv("AUTHND_DEFAULT_PUBLISHER", raising=False)
    assert a._normalize_model(raw) == expected


def test_default_publisher_defined_regression():
    # Previously `_normalize_model("bare")` raised NameError: DEFAULT_PUBLISHER undefined.
    assert a.DEFAULT_PUBLISHER == "moonshotai"
    assert a._normalize_model("bare-model")[0] == "moonshotai"


def test_predict_gateway_and_redirect_rejection():
    assert a.PREDICT_API_BASE_URL == "https://buildapi.ngc.nvidia.com"

    class R:
        status_code = 302
        headers = {"location": "https://ngc.nvidia.com/404"}
        text = ""
        reason = "Found"

    with pytest.raises(RuntimeError, match=r"redirected to https://ngc\.nvidia\.com/404"):
        a._raise_for_status(R())

    class Ok:
        status_code = 200
        headers: Dict[str, str] = {}
        text = ""
        reason = "OK"

    a._raise_for_status(Ok())


# ------------------------------------------------------------ dependencies
def test_missing_dependency_diagnostics(monkeypatch):
    def fake_import():
        raise ImportError("No module named PySide6")

    monkeypatch.setenv("AUTHND_TOKEN_MODE", "subprocess")
    monkeypatch.setattr(a, "_import_qt_webengine", fake_import)
    status = a.token_helper_status()
    assert status["available"] is False and "PySide6" in status["detail"]
    with pytest.raises(a.AuthNDDependencyError):
        a.get_captcha_token("https://build.nvidia.com/moonshotai/kimi-k3", timeout=5)

    from app.api.endpoints.authnd import compute_authnd_status

    assert compute_authnd_status(probe_network=False)["status"] == "missing_dependency"


def test_status_ready_when_helper_available(monkeypatch):
    monkeypatch.setattr(a, "token_helper_status", lambda: {"mode": "subprocess", "available": True, "detail": "ok"})
    from app.api.endpoints.authnd import compute_authnd_status

    assert compute_authnd_status(probe_network=False)["status"] == "ready"


def test_pool_mode_rejects_empty_token(monkeypatch):
    monkeypatch.setenv("AUTHND_TOKEN_MODE", "pool")

    class Resp:
        content = b"{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {"token": ""}

    monkeypatch.setattr(a.requests, "post", lambda *args, **kwargs: Resp())
    with pytest.raises(RuntimeError, match="empty token"):
        a.get_captcha_token("https://build.nvidia.com/x/y", timeout=5)


def test_token_validation():
    with pytest.raises(RuntimeError):
        a._validate_token("")
    with pytest.raises(RuntimeError):
        a._validate_token("short")
    assert a._validate_token("P1_" + "x" * 40) == "P1_" + "x" * 40


# ------------------------------------------------------------ fake transport
class _Fake:
    """Replace token minting and the NVIDIA POST with an in-process fake."""

    def __init__(self, monkeypatch, *, reply: str = "hello", delay: float = 0.0, fail_first: int = 0, block_event: threading.Event | None = None):
        self.reply = reply
        self.delay = delay
        self.fail_first = fail_first
        self.calls: List[Dict[str, Any]] = []
        self.token_calls = 0
        self.block_event = block_event
        monkeypatch.setenv("AUTHND_TOKEN_RETRIES", "3")
        monkeypatch.setattr(a, "_run_with_token_slot", self._token)
        monkeypatch.setattr(a, "_post_prediction", self._post)

    def _token(self, fn, proxy=None):
        self.token_calls += 1
        return "P1_" + "t" * 40

    def _post(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RuntimeError("AuthND HTTP 400: captcha token rejected")
        cb = kwargs.get("chunk_callback")
        text = self.reply if isinstance(self.reply, str) else self.reply()
        for piece in [text[i:i + 5] for i in range(0, len(text), 5)]:
            if a._is_cancelled():
                raise RuntimeError("stream cancelled")
            if self.block_event is not None:
                self.block_event.wait(timeout=5)
            elif self.delay:
                time.sleep(self.delay)
            if cb:
                cb(piece, None)
        return {"content": text, "reasoning_content": None, "finish_reason": "stop", "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


def test_send_chat_completion_success_and_usage(monkeypatch):
    fake = _Fake(monkeypatch, reply="hello world")
    res = a.send_chat_completion(messages=[{"role": "user", "content": "hi"}], model="moonshotai/kimi-k3", stream=False)
    assert res["content"] == "hello world" and res["usage"]["total_tokens"] == 18 and res["model"] == "kimi-k3"
    assert fake.token_calls == 1 and len(fake.calls) == 1


def test_retry_gets_fresh_token_and_reports_attempts(monkeypatch):
    fake = _Fake(monkeypatch, reply="ok", fail_first=1)
    res = a.send_chat_completion(messages=[{"role": "user", "content": "hi"}], model="moonshotai/kimi-k3", stream=False)
    assert res["content"] == "ok" and fake.token_calls == 2 and len(fake.calls) == 2
    fake2 = _Fake(monkeypatch, reply="ok", fail_first=99)
    with pytest.raises(RuntimeError) as exc:
        a.send_chat_completion(messages=[{"role": "user", "content": "hi"}], model="moonshotai/kimi-k3", stream=False)
    assert "attempts" in str(exc.value) and "request attempt 3" in str(exc.value)
    assert fake2.token_calls == 3


def test_request_scoped_cancellation_does_not_affect_other_request(monkeypatch):
    gate = threading.Event()
    fake = _Fake(monkeypatch, reply="x" * 200, block_event=gate)
    results: Dict[str, Any] = {}
    c1, c2 = a.RequestControl("one"), a.RequestControl("two")

    def run(name, control):
        try:
            results[name] = a.send_chat_completion(messages=[{"role": "user", "content": name}], model="moonshotai/kimi-k3", stream=True, chunk_callback=lambda t, r: None, control=control)
        except Exception as exc:
            results[name] = exc

    t1 = threading.Thread(target=run, args=("one", c1))
    t2 = threading.Thread(target=run, args=("two", c2))
    t1.start()
    t2.start()
    time.sleep(0.2)
    c1.cancel()
    # reset_cancel() must not un-cancel a request-scoped control
    a.reset_cancel()
    assert c1.is_cancelled()
    gate.set()
    t1.join(10)
    t2.join(10)
    assert isinstance(results["one"], RuntimeError) and "cancelled" in str(results["one"])
    assert isinstance(results["two"], dict) and results["two"]["content"] == "x" * 200
    assert not a._is_cancelled()  # no leaked global state


def test_concurrent_requests_isolated(monkeypatch):
    fake = _Fake(monkeypatch, reply=lambda: threading.current_thread().name)
    out: Dict[str, str] = {}

    def run(i):
        r = a.send_chat_completion(messages=[{"role": "user", "content": str(i)}], model="moonshotai/kimi-k3", stream=False)
        out[threading.current_thread().name] = r["content"]

    threads = [threading.Thread(target=run, args=(i,), name=f"req-{i}") for i in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(out) == 6 and all(k == v for k, v in out.items())


def test_subprocess_helper_timeout_kills_child(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(a, "_is_frozen_app", lambda: True)
    monkeypatch.setattr(a.sys, "executable", sys.executable)
    monkeypatch.setattr(a, "_get_token_subprocess_semaphore", lambda: threading.BoundedSemaphore(1))
    # Replace the helper command with a sleeper: the command list is built from
    # sys.executable + flags; "-c" is appended via the frozen-app branch args.
    real_popen = a.subprocess.Popen

    def sleepy_popen(cmd, **kwargs):
        return real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)

    monkeypatch.setattr(a.subprocess, "Popen", sleepy_popen)
    monkeypatch.setattr(a.time, "time", _FastClock().time)
    with pytest.raises(RuntimeError, match="timed out"):
        a._mint_captcha_token_subprocess("https://build.nvidia.com/x/y", timeout=1)


class _FastClock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        self.t += 20.0
        return self.t


def test_subprocess_helper_cancel_kills_child(monkeypatch):
    import sys

    monkeypatch.setattr(a, "_is_frozen_app", lambda: True)
    monkeypatch.setattr(a, "_get_token_subprocess_semaphore", lambda: threading.BoundedSemaphore(1))
    real_popen = a.subprocess.Popen
    procs = []

    def sleepy_popen(cmd, **kwargs):
        p = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        procs.append(p)
        return p

    monkeypatch.setattr(a.subprocess, "Popen", sleepy_popen)
    control = a.RequestControl("cancel-me")
    token = a._current_control.set(control)
    try:
        threading.Timer(0.3, control.cancel).start()
        with pytest.raises(RuntimeError, match="cancelled"):
            a._mint_captcha_token_subprocess("https://build.nvidia.com/x/y", timeout=30)
    finally:
        a._current_control.reset(token)
    assert procs and procs[0].poll() is not None  # child is gone


# ------------------------------------------------------------ SSE endpoint
def _sse_events(body: str) -> List[Any]:
    out = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            payload = line[6:]
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


@pytest.fixture
def api(app_client):
    return app_client


def test_sse_success_terminates_once(api, monkeypatch):
    _Fake(monkeypatch, reply="streamed reply")
    r = api.post("/api/authnd/v1/chat/completions", json={"model": "moonshotai/kimi-k3", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert r.status_code == 200
    events = _sse_events(r.text)
    assert events[-1] == "[DONE]"
    finishes = [e["choices"][0]["finish_reason"] for e in events[:-1] if e["choices"][0]["finish_reason"]]
    assert finishes == ["stop"]
    assert "".join(e["choices"][0]["delta"].get("content", "") for e in events[:-1]) == "streamed reply"


def test_sse_error_has_no_stop(api, monkeypatch):
    _Fake(monkeypatch, reply="x", fail_first=99)
    r = api.post("/api/authnd/v1/chat/completions", json={"model": "moonshotai/kimi-k3", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    events = _sse_events(r.text)
    assert events[-1] == "[DONE]"
    finishes = [e["choices"][0]["finish_reason"] for e in events[:-1] if e["choices"][0]["finish_reason"]]
    assert finishes == ["error"]
    assert "[Error:" in events[-2]["choices"][0]["delta"]["content"]


def test_sse_cancellation_terminates_with_error(api, monkeypatch):
    def cancelled_post(**kwargs):
        raise RuntimeError("stream cancelled")

    monkeypatch.setenv("AUTHND_TOKEN_RETRIES", "2")
    monkeypatch.setattr(a, "_run_with_token_slot", lambda fn, proxy=None: "P1_" + "t" * 40)
    monkeypatch.setattr(a, "_post_prediction", cancelled_post)
    r = api.post("/api/authnd/v1/chat/completions", json={"model": "moonshotai/kimi-k3", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    events = _sse_events(r.text)
    finishes = [e["choices"][0]["finish_reason"] for e in events[:-1] if e["choices"][0]["finish_reason"]]
    assert finishes == ["error"] and events[-1] == "[DONE]"


def test_non_stream_error_status(api, monkeypatch):
    _Fake(monkeypatch, reply="x", fail_first=99)
    r = api.post("/api/authnd/v1/chat/completions", json={"model": "moonshotai/kimi-k3", "messages": [{"role": "user", "content": "hi"}], "stream": False})
    assert r.status_code == 502


def test_status_endpoint_truthful(api, monkeypatch):
    monkeypatch.setattr(a, "token_helper_status", lambda: {"mode": "subprocess", "available": False, "detail": "helper binary missing"})
    r = api.get("/api/authnd/status", params={"probe": "false"})
    assert r.json()["data"]["status"] == "token_helper_unavailable"


# ------------------------------------------------------------ structured
class Out(BaseModel):
    title: str
    score: int = Field(ge=0, le=10)
    tags: List[str] = Field(default_factory=list)


def test_parse_structured_text_repair_and_validation():
    assert parse_structured_text('Sure! ```json\n{"title": "A", "score": 3, "tags": ["x"]}\n```', Out).score == 3
    # trailing comma + single quotes -> repaired
    assert parse_structured_text("{'title': 'B', 'score': 4, 'tags': [],}", Out).title == "B"
    with pytest.raises(StructuredOutputError):
        parse_structured_text('{"title": "C", "score": 42}', Out)  # out of range
    with pytest.raises(StructuredOutputError):
        parse_structured_text("no json here", Out)


def test_chat_authnd_structured_fallback_and_retry(monkeypatch):
    replies = iter(['{"title": "bad", "score": 99}', '{"title": "good", "score": 5, "tags": ["a"]}'])
    fake = _Fake(monkeypatch, reply=lambda: next(replies))
    model = ChatAuthND(model="moonshotai/kimi-k3")
    runnable = model.with_structured_output(Out)
    out = runnable.invoke("make one")
    assert isinstance(out, Out) and out.title == "good"
    assert len(fake.calls) == 2
    # Schema instruction was injected as a system message.
    assert any("JSON Schema" in m["content"] for m in fake.calls[0]["messages"])  # system text folded into the first user turn
    # The retry carried the validation error back to the model.
    assert any("rejected" in m["content"] for m in fake.calls[1]["messages"] if m["role"] == "user")


def test_chat_authnd_structured_final_failure_raises(monkeypatch):
    _Fake(monkeypatch, reply="I cannot comply.")
    model = ChatAuthND(model="moonshotai/kimi-k3", structured_max_attempts=2)
    with pytest.raises(StructuredOutputError):
        model.with_structured_output(Out).invoke("x")
    env = model.with_structured_output(Out, include_raw=True).invoke("x")
    assert env["parsed"] is None and isinstance(env["parsing_error"], StructuredOutputError)


def test_generate_structured_via_llm_config_records_usage_once(monkeypatch, app_client):
    """Full path: LLMConfig(provider=authnd) -> build_chat_model -> generate_structured."""
    from sqlmodel import Session, select

    from app.db.models import LLMConfig
    from app.db.session import engine
    from app.services.ai.core.llm_service import generate_structured

    replies = iter(["garbage", '{"title": "Z", "score": 1, "tags": []}'])
    _Fake(monkeypatch, reply=lambda: next(replies))
    with Session(engine) as s:
        cfg = LLMConfig(provider="authnd", display_name="t", model_name="moonshotai/kimi-k3", api_key="")
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
        out = asyncio.run(generate_structured(session=s, llm_config_id=cfg.id, user_prompt="p", output_type=Out, max_retries=2))
        assert isinstance(out, Out) and out.title == "Z"
        s.refresh(cfg)
        # Two model round-trips (repair retry) but ONE logical request -> one call recorded,
        # and real usage from the provider (not an estimate).
        assert cfg.used_calls == 1
        assert cfg.used_tokens_output == 7 and cfg.used_tokens_input == 11


def test_generate_structured_failure_records_once(monkeypatch, app_client):
    from sqlmodel import Session

    from app.db.models import LLMConfig
    from app.db.session import engine
    from app.services.ai.core.llm_service import generate_structured

    _Fake(monkeypatch, reply="never json")
    with Session(engine) as s:
        cfg = LLMConfig(provider="authnd", display_name="t2", model_name="moonshotai/kimi-k3", api_key="")
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
        with pytest.raises(ValueError, match="attempts"):
            asyncio.run(generate_structured(session=s, llm_config_id=cfg.id, user_prompt="p", output_type=Out, max_retries=2))
        s.refresh(cfg)
        assert cfg.used_calls == 1


def test_build_chat_model_uses_authnd_for_llm_config(app_client):
    from sqlmodel import Session

    from app.db.models import LLMConfig
    from app.db.session import engine
    from app.services.ai.core.chat_model_factory import build_chat_model

    with Session(engine) as s:
        cfg = LLMConfig(provider="authnd", display_name="t3", model_name="", api_key="")
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
        model = build_chat_model(s, cfg.id)
        assert isinstance(model, ChatAuthND) and model.model_name == "moonshotai/kimi-k3"
