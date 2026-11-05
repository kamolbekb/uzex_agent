"""LLM Router with priority chain, automatic provider/model fallback, cooldowns, and quota tracking."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
CALLS_LOG_PATH = LOGS_DIR / "llm_calls.jsonl"
USAGE_PATH = LOGS_DIR / "usage.json"


@dataclass
class ChainSlot:
    provider: str
    model: str
    key_env: str
    api_key: str
    base_url: str
    daily_limit: int = 1000
    status: str = "ok"  # "ok", "cooldown", "disabled"
    cooldown_until: float = 0.0
    last_error: str = ""
    calls_ok: int = 0
    calls_err: int = 0

    @property
    def slot_id(self) -> str:
        return f"{self.provider}/{self.model}[{self.key_env}]"

    def is_available(self, now: float | None = None) -> bool:
        if self.status == "disabled":
            return False
        now = time.time() if now is None else now
        if self.cooldown_until > now:
            return False
        if self.status == "cooldown" and self.cooldown_until <= now:
            self.status = "ok"
        return True


@dataclass
class RouterResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw_message: Any = None


def _next_pacific_midnight_ts() -> float:
    """Return Unix timestamp of next midnight US Pacific Time (UTC-8/UTC-7, approx UTC-7/-8)."""
    now_utc = datetime.now(timezone.utc)
    # Pacific standard/daylight is UTC-8 in November (after first Sunday of Nov, or UTC-7 before)
    # Using UTC-8 (08:00 UTC = 13:00 Tashkent) as safe reset mark
    target_utc = now_utc.replace(hour=8, minute=5, second=0, microsecond=0)
    if target_utc <= now_utc:
        target_utc += timedelta(days=1)
    return target_utc.timestamp()


def _pacific_day_key() -> str:
    """Return YYYY-MM-DD in US Pacific Time (UTC-8) for daily quota tracking."""
    now_pt = datetime.now(timezone(timedelta(hours=-8)))
    return now_pt.strftime("%Y-%m-%d")


class LLMRouter:
    """Priority-ordered multi-provider LLM router with automatic fallback and quota protection."""

    def __init__(
        self,
        config_path: Path | None = None,
        client_factory: Callable[[str, str, float], Any] | None = None,
    ) -> None:
        load_dotenv(PROJECT_ROOT / ".env")
        self.config_path = config_path or (PROJECT_ROOT / "config.yaml")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

        timeouts = self.cfg.get("timeouts", {})
        self.per_call_s: float = float(timeouts.get("per_call_s", 25.0))
        self.total_budget_s: float = float(timeouts.get("total_budget_s", 70.0))

        self._client_factory = client_factory or (
            lambda api_key, base_url, timeout: OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0
            )
        )
        self._clients: dict[tuple[str, str], Any] = {}
        self.slots: list[ChainSlot] = []
        self._build_slots()
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def _build_slots(self) -> None:
        providers_cfg = self.cfg.get("providers", {})
        chain_cfg = self.cfg.get("chain", [])
        for entry in chain_cfg:
            prov = entry["provider"]
            model = entry["model"]
            daily_limit = int(entry.get("daily_limit", 1000))
            pinfo = providers_cfg.get(prov, {})
            base_url = pinfo.get("base_url", "")
            keys_env = pinfo.get("keys_env", [])
            for k_env in keys_env:
                api_key = os.getenv(k_env, "").strip()
                if not api_key:
                    continue
                slot = ChainSlot(
                    provider=prov,
                    model=model,
                    key_env=k_env,
                    api_key=api_key,
                    base_url=base_url,
                    daily_limit=daily_limit,
                )
                self.slots.append(slot)

    def _get_client(self, slot: ChainSlot) -> Any:
        key = (slot.provider, slot.api_key)
        if key not in self._clients:
            self._clients[key] = self._client_factory(slot.api_key, slot.base_url, self.per_call_s)
        return self._clients[key]

    # ------------------------------------------------------------------
    # Daily Usage Tracking
    # ------------------------------------------------------------------
    def _load_usage(self) -> dict[str, Any]:
        day = _pacific_day_key()
        if USAGE_PATH.exists():
            try:
                data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
                if data.get("day") == day:
                    return data
            except Exception:
                pass
        return {"day": day, "models": {}}

    def _increment_usage(self, slot: ChainSlot, tokens: int) -> None:
        data = self._load_usage()
        mkey = f"{slot.provider}/{slot.model}"
        m_entry = data["models"].setdefault(mkey, {"calls": 0, "tokens": 0})
        m_entry["calls"] += 1
        m_entry["tokens"] += tokens
        try:
            USAGE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_model_daily_calls(self, slot: ChainSlot) -> int:
        data = self._load_usage()
        mkey = f"{slot.provider}/{slot.model}"
        return int(data.get("models", {}).get(mkey, {}).get("calls", 0))

    def _ordered_available_slots(self, now: float) -> list[ChainSlot]:
        """Return available slots, demoting any slot above 80% of its daily call limit."""
        normal: list[ChainSlot] = []
        demoted: list[ChainSlot] = []
        for s in self.slots:
            if not s.is_available(now):
                continue
            used = self.get_model_daily_calls(s)
            if s.daily_limit > 0 and used >= int(0.8 * s.daily_limit):
                demoted.append(s)
            else:
                normal.append(s)
        return normal + demoted

    # ------------------------------------------------------------------
    # Logging & Error Classification
    # ------------------------------------------------------------------
    def _log_call(
        self,
        slot: ChainSlot,
        latency_s: float,
        outcome: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: str = "",
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": slot.provider,
            "model": slot.model,
            "key_env": slot.key_env,
            "latency_s": round(latency_s, 3),
            "outcome": outcome,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "error": error[:300] if error else "",
        }
        try:
            with open(CALLS_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _handle_error(self, slot: ChainSlot, exc: Exception) -> None:
        now = time.time()
        err_str = f"{type(exc).__name__}: {exc}"
        slot.calls_err += 1
        slot.last_error = err_str[:250]

        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            slot.status = "disabled"
            slot.cooldown_until = float("inf")
            return

        if isinstance(exc, NotFoundError):
            # Model doesn't exist or isn't accessible -> disable for session
            slot.status = "disabled"
            slot.cooldown_until = float("inf")
            return

        if isinstance(exc, RateLimitError):
            retry_after = 12.0
            # Check headers if present
            resp = getattr(exc, "response", None)
            if resp is not None and hasattr(resp, "headers"):
                ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
                if ra:
                    try:
                        retry_after = max(1.0, float(ra))
                    except ValueError:
                        pass
            msg_low = str(exc).lower()
            if "per day" in msg_low or "daily" in msg_low or "rpd" in msg_low:
                slot.cooldown_until = _next_pacific_midnight_ts()
            else:
                # Also parse "try again in Xms" or "try again in Xs" from Groq error text
                m_ms = re.search(r"try again in\s+([0-9.]+)ms", msg_low)
                m_s = re.search(r"try again in\s+([0-9.]+)s\b", msg_low)
                if m_ms:
                    retry_after = max(1.0, float(m_ms.group(1)) / 1000.0 + 0.5)
                elif m_s:
                    retry_after = max(1.0, float(m_s.group(1)) + 1.0)
                slot.cooldown_until = now + retry_after
            slot.status = "cooldown"
            return

        if isinstance(exc, BadRequestError):
            # e.g. context length exceeded or unsupported tool format on this specific model -> short cooldown
            slot.status = "cooldown"
            slot.cooldown_until = now + 15.0
            return

        if isinstance(exc, (InternalServerError, APITimeoutError, APIConnectionError, APIStatusError)):
            slot.status = "cooldown"
            slot.cooldown_until = now + 30.0
            return

        # Any other unexpected exception
        slot.status = "cooldown"
        slot.cooldown_until = now + 30.0

    # ------------------------------------------------------------------
    # Main Call Method
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        validator: Callable[[RouterResponse], bool] | None = None,
        deadline_ts: float | None = None,
    ) -> RouterResponse:
        """Execute a chat completion across the priority chain with automatic fallback.

        - If `validator` is provided and returns False, retries once on the same model, then moves to next slot.
        - Respects `deadline_ts` (or `self.total_budget_s` from start of call).
        """
        start_ts = time.time()
        if deadline_ts is None:
            deadline_ts = start_ts + self.total_budget_s

        errors: list[str] = []
        while True:
            now = time.time()
            if deadline_ts - now <= 1.5:
                errors.append("Global time budget exhausted")
                break

            candidates = self._ordered_available_slots(now)
            if not candidates:
                # All non-disabled slots are in cooldown -> wait for the soonest cooldown if within budget
                recoverable = [s for s in self.slots if s.status != "disabled"]
                if not recoverable:
                    break
                recoverable.sort(key=lambda x: x.cooldown_until)
                soonest = recoverable[0]
                wait_s = max(0.0, soonest.cooldown_until - now)
                if now + wait_s + 2.0 < deadline_ts:
                    time.sleep(min(wait_s + 0.1, 15.0))
                    soonest.status = "ok"
                    candidates = [soonest]
                else:
                    candidates = [soonest]

            for slot in candidates:
                now = time.time()
                remaining = deadline_ts - now
                if remaining <= 1.5:
                    break

                client = self._get_client(slot)
                call_timeout = min(self.per_call_s, max(2.0, remaining - 0.5))

                # Allow up to 2 attempts on the same slot ONLY when output validation fails
                for attempt in range(2):
                    t0 = time.perf_counter()
                    try:
                        # Sanitize messages for provider compatibility
                        clean_msgs = []
                        is_gemini_3 = slot.provider == "gemini" and (
                            slot.model.startswith("gemini-3") or slot.model.startswith("gemini-4")
                        )
                        for m in messages:
                            if not isinstance(m, dict):
                                clean_msgs.append(m)
                                continue
                            if is_gemini_3 and "tool_calls" in m and "extra_content" not in m:
                                # Gemini 3 requires thought_signature on assistant tool_calls; convert prior turn to user context
                                tc_summary = json.dumps(m.get("tool_calls", []), ensure_ascii=False)
                                clean_msgs.append({"role": "user", "content": f"[Previous step executed tool calls: {tc_summary}]"})
                            elif is_gemini_3 and m.get("role") == "tool" and clean_msgs and "tool_calls" not in clean_msgs[-1]:
                                clean_msgs.append({"role": "user", "content": f"[Tool {m.get('name')} returned: {m.get('content')}]"})
                            elif slot.provider != "gemini":
                                m_copy = {k: v for k, v in m.items() if k not in ("extra_content", "function_call", "audio")}
                                if m_copy.get("role") == "assistant" and "content" not in m_copy:
                                    m_copy["content"] = ""
                                if "tool_calls" in m_copy and isinstance(m_copy["tool_calls"], list):
                                    clean_tcs = []
                                    for tc in m_copy["tool_calls"]:
                                        if isinstance(tc, dict):
                                            clean_tcs.append({k: v for k, v in tc.items() if k in ("id", "type", "function")})
                                        else:
                                            clean_tcs.append(tc)
                                    m_copy["tool_calls"] = clean_tcs
                                clean_msgs.append(m_copy)
                            else:
                                clean_msgs.append(m)

                        kwargs: dict[str, Any] = {
                            "model": slot.model,
                            "messages": clean_msgs,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "timeout": call_timeout,
                        }
                        if tools:
                            kwargs["tools"] = tools
                            kwargs["tool_choice"] = "auto"

                        resp = client.chat.completions.create(**kwargs)
                        dt = time.perf_counter() - t0

                        choice = resp.choices[0]
                        msg = choice.message
                        content = (msg.content or "").strip()

                        parsed_tools: list[dict[str, Any]] = []
                        if getattr(msg, "tool_calls", None):
                            for tc in msg.tool_calls:
                                parsed_tools.append(
                                    {
                                        "id": tc.id,
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    }
                                )

                        usage = getattr(resp, "usage", None)
                        p_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
                        c_tok = int(getattr(usage, "completion_tokens", 0) or 0)
                        t_tok = int(getattr(usage, "total_tokens", p_tok + c_tok) or (p_tok + c_tok))

                        result = RouterResponse(
                            content=content,
                            tool_calls=parsed_tools,
                            provider=slot.provider,
                            model=slot.model,
                            latency_s=dt,
                            prompt_tokens=p_tok,
                            completion_tokens=c_tok,
                            total_tokens=t_tok,
                            raw_message=msg,
                        )

                        # Empty response without tool calls or failed custom validator
                        if not content and not parsed_tools:
                            raise ValueError("Empty content and no tool_calls returned")

                        if validator is not None and not validator(result):
                            raise ValueError("Response failed output validator")

                        slot.calls_ok += 1
                        slot.status = "ok"
                        slot.cooldown_until = 0.0
                        self._increment_usage(slot, t_tok)
                        self._log_call(slot, dt, "ok", p_tok, c_tok)
                        return result

                    except ValueError as ve:
                        dt = time.perf_counter() - t0
                        self._log_call(slot, dt, "bad_output", error=str(ve))
                        errors.append(f"{slot.slot_id} (attempt {attempt + 1}): {ve}")
                        if attempt == 0 and (deadline_ts - time.time()) > 3.0:
                            continue  # retry once on same model for bad output
                        slot.calls_err += 1
                        slot.last_error = str(ve)
                        break

                    except Exception as exc:
                        dt = time.perf_counter() - t0
                        self._handle_error(slot, exc)
                        self._log_call(slot, dt, "error", error=f"{type(exc).__name__}: {exc}")
                        errors.append(f"{slot.slot_id}: {type(exc).__name__}: {exc}")
                        break

            # If we looped through all current candidates without returning, check if we still have time to wait for a short cooldown
            recoverable = [s for s in self.slots if s.status != "disabled"]
            if not recoverable:
                break
            soonest = min(recoverable, key=lambda x: x.cooldown_until)
            wait_s = max(0.0, soonest.cooldown_until - time.time())
            if time.time() + wait_s + 2.5 < deadline_ts and wait_s <= 20.0:
                time.sleep(wait_s + 0.1)
                soonest.status = "ok"
                continue
            break

        raise RuntimeError(f"All LLM chain slots failed. Details: {' | '.join(errors)}")

    def get_status_report(self) -> list[dict[str, Any]]:
        """Return health & usage summary for `/status` command."""
        now = time.time()
        out = []
        for s in self.slots:
            rem_cd = max(0, int(s.cooldown_until - now)) if s.cooldown_until != float("inf") else -1
            state = "ok"
            if s.status == "disabled":
                state = "disabled"
            elif rem_cd > 0:
                state = f"cooldown ({rem_cd}s)"
            out.append(
                {
                    "provider": s.provider,
                    "model": s.model,
                    "state": state,
                    "calls_ok": s.calls_ok,
                    "calls_err": s.calls_err,
                    "daily_calls": self.get_model_daily_calls(s),
                    "last_error": s.last_error,
                }
            )
        return out
