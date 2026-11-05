"""Verify LLM providers and candidate models defined in config.yaml."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict[str, Any]:
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_models() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    cfg = load_config()
    providers_cfg = cfg.get("providers", {})
    chain = cfg.get("chain", [])

    clients: dict[str, OpenAI] = {}
    available_models: dict[str, set[str]] = {}

    print("=== 1. Checking Provider Keys & Listing Models ===")
    for prov_name, prov_info in providers_cfg.items():
        base_url = prov_info["base_url"]
        keys_env = prov_info.get("keys_env", [])
        api_key = None
        for env_name in keys_env:
            val = os.getenv(env_name, "").strip()
            if val:
                api_key = val
                break

        if not api_key:
            print(f"[WARN] {prov_name}: No API key found in {keys_env}")
            continue

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=20.0, max_retries=0)
        clients[prov_name] = client
        try:
            t0 = time.perf_counter()
            models_resp = client.models.list()
            dt = time.perf_counter() - t0
            ids = {m.id for m in models_resp.data}
            # Gemini OpenAI endpoint sometimes returns "models/gemini-..."
            normalized_ids = set(ids)
            for mid in ids:
                if mid.startswith("models/"):
                    normalized_ids.add(mid.removeprefix("models/"))
            available_models[prov_name] = normalized_ids
            print(f"[OK]   {prov_name}: listed {len(ids)} models in {dt:.2f}s")
            sample = sorted(normalized_ids)
            for i in range(0, len(sample), 10):
                print(f"       {', '.join(sample[i:i+10])}")
        except Exception as e:
            print(f"[FAIL] {prov_name}: failed to list models: {type(e).__name__}: {e}")
            available_models[prov_name] = set()

    print("\n=== 2. Pinging Configured Chain Entries ===")
    ok_count = 0
    for idx, entry in enumerate(chain, 1):
        prov = entry["provider"]
        model = entry["model"]
        client = clients.get(prov)
        if not client:
            print(f"[{idx}] [SKIP] {prov}/{model} — provider client not initialized")
            continue

        in_list = model in available_models.get(prov, set())
        list_tag = "listed" if in_list else "NOT in /models"

        try:
            t0 = time.perf_counter()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with the exact word PONG and nothing else."}],
                temperature=0.0,
                max_tokens=256,
            )
            dt = time.perf_counter() - t0
            text = (resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            tok_info = f"tokens={usage.total_tokens}" if usage else "tokens=?"
            print(f"[{idx}] [OK]   {prov}/{model} ({list_tag}) -> {text!r} in {dt:.2f}s ({tok_info})")
            ok_count += 1
        except Exception as e:
            print(f"[{idx}] [FAIL] {prov}/{model} ({list_tag}) -> {type(e).__name__}: {e}")

    print(f"\nSummary: {ok_count}/{len(chain)} chain models responded OK.")
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(check_models())
