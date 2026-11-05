"""Chaos test for LLMRouter: verify fallback on 401 bad key, 429 rate limit, 500 timeout, and bad output."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.llm_router import LLMRouter, RouterResponse  # noqa: E402


class FakeCompletions:
    def __init__(self, behavior_fn):
        self.behavior_fn = behavior_fn

    def create(self, **kwargs: Any) -> Any:
        return self.behavior_fn(**kwargs)


class FakeClient:
    def __init__(self, behavior_fn):
        self.chat = SimpleNamespace(completions=FakeCompletions(behavior_fn))


def _make_httpx_resp(status_code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    return httpx.Response(status_code=status_code, headers=headers or {}, request=req)


def _ok_completion(text: str) -> Any:
    msg = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=msg)
    usage = SimpleNamespace(prompt_tokens=15, completion_tokens=5, total_tokens=20)
    return SimpleNamespace(choices=[choice], usage=usage)


def run_chaos_tests() -> None:
    print("=== Running LLMRouter Chaos Tests ===")

    # Scenario 1: Slot 1 -> 401 AuthError, Slot 2 -> 429 RateLimit (Retry-After: 45), Slot 3 -> Timeout, Slot 4 -> OK
    call_log: list[str] = []

    def behavior(**kwargs: Any) -> Any:
        model = kwargs["model"]
        call_log.append(model)
        if model == "gemini-2.5-flash":
            raise AuthenticationError(
                "Invalid API key",
                response=_make_httpx_resp(401),
                body={"error": {"message": "Invalid API key"}},
            )
        if model == "openai/gpt-oss-120b":
            raise RateLimitError(
                "Rate limit exceeded, please retry",
                response=_make_httpx_resp(429, {"Retry-After": "45"}),
                body={"error": {"message": "Rate limit exceeded"}},
            )
        if model == "gemini-3.5-flash":
            raise APITimeoutError(httpx.Request("POST", "https://example.com"))
        if model == "qwen/qwen3.8-27b":
            return _ok_completion("FALLBACK_SUCCESS")
        return _ok_completion("DEFAULT_OK")

    router = LLMRouter(client_factory=lambda key, url, timeout: FakeClient(behavior))
    t0 = time.perf_counter()
    resp = router.chat(messages=[{"role": "user", "content": "test"}])
    dt = time.perf_counter() - t0

    assert resp.content == "FALLBACK_SUCCESS", f"Unexpected content: {resp.content}"
    assert resp.model == "qwen/qwen3.8-27b", f"Unexpected model: {resp.model}"
    assert call_log == [
        "gemini-2.5-flash",
        "openai/gpt-oss-120b",
        "gemini-3.5-flash",
        "qwen/qwen3.8-27b",
    ], f"Unexpected call sequence: {call_log}"

    # Verify slot states after errors
    s0, s1, s2, s3 = router.slots[:4]
    assert s0.status == "disabled", f"Expected disabled on 401, got {s0.status}"
    assert s1.status == "cooldown" and (s1.cooldown_until - time.time()) >= 40, "Expected ~45s cooldown on 429"
    assert s2.status == "cooldown" and (s2.cooldown_until - time.time()) >= 25, "Expected ~30s cooldown on Timeout"
    assert s3.status == "ok" and s3.calls_ok == 1, "Expected ok on slot 4"
    print(f"  [OK] Scenario 1 (401 -> 429 -> Timeout -> Success on 4th model) passed in {dt*1000:.1f}ms")

    # Scenario 2: Subsequent call skips disabled and cooled-down slots immediately
    call_log.clear()
    resp2 = router.chat(messages=[{"role": "user", "content": "second call"}])
    assert call_log == ["qwen/qwen3.8-27b"], f"Should have gone straight to qwen/qwen3.8-27b, got {call_log}"
    print("  [OK] Scenario 2 (Cooldown & disabled slots skipped on next call) passed")

    # Scenario 3: Invalid output retries once on same model, then falls back to next model
    bad_attempts = {"count": 0}

    def bad_output_behavior(**kwargs: Any) -> Any:
        model = kwargs["model"]
        bad_attempts["count"] += 1
        if model == "gemini-2.5-flash":
            return _ok_completion("NOT_JSON")
        return _ok_completion('{"valid": true}')

    router_val = LLMRouter(client_factory=lambda key, url, timeout: FakeClient(bad_output_behavior))
    resp3 = router_val.chat(
        messages=[{"role": "user", "content": "give json"}],
        validator=lambda r: r.content.startswith("{"),
    )
    assert resp3.content == '{"valid": true}'
    assert bad_attempts["count"] == 3, f"Expected 2 attempts on slot 1 + 1 on slot 2 = 3, got {bad_attempts['count']}"
    print("  [OK] Scenario 3 (Bad output retried once on same model, then fallback) passed")

    print("\nALL CHAOS TESTS PASSED!")


if __name__ == "__main__":
    run_chaos_tests()
