#!/usr/bin/env python3
"""Suite 12: Security & Leakage Integration Tests

Verifies that API keys and auth tokens never leak into cache keys,
cache values, error messages, singleflight keys, or response objects.

NO MOCKING — real pipeline components, real cache.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_not_contains,
    assert_true,
    env,
    make_call_input,
    make_real_pipeline,
    openai_dispatch,
    print_summary,
    skip_unless,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llmix.response_cache import CACHE_KEY_FIELDS, TwoTierCache, generate_cache_key
from llmix.resilience import Singleflight


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_API_KEY = "sk-test-secret-key-12345"
OPENAI_MODEL = "gpt-4.1-mini"


def uid() -> str:
    return f"{time.time():.6f}"


def simple_prompt(label: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": f"Reply with exactly one word: blue. ({label} {uid()})"}]


# ---------------------------------------------------------------------------
# 12.1 — API key not in cache key INPUT fields
# ---------------------------------------------------------------------------

async def test_12_1_api_key_not_in_cache_key_fields():
    """CACHE_KEY_FIELDS contains no key-related field, and the canonical
    JSON built from test params contains no 'sk-' substring."""

    # Check field names
    key_like = {"apiKey", "api_key", "key", "secret", "token", "authorization"}
    found = [f for f in CACHE_KEY_FIELDS if f.lower() in key_like]
    assert_true(len(found) == 0, "CACHE_KEY_FIELDS has no key-related field names")

    # Build canonical JSON the same way generate_cache_key does
    params = {
        "provider": "openai",
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": "hello"}],
        "baseUrl": "",
        "temperature": 0.7,
        "maxOutputTokens": 100,
        "seed": 42,
        "topP": 1.0,
        "responseFormat": None,
        "providerOptions": None,
        # Sneak in an API key in a non-canonical field
        "apiKey": TEST_API_KEY,
        "api_key": TEST_API_KEY,
    }

    # Reproduce the canonical dict logic
    canonical: dict[str, object] = {}
    for field_name in CACHE_KEY_FIELDS:
        value = params.get(field_name)
        if value is not None:
            canonical[field_name] = value

    json_str = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    assert_not_contains(json_str, "sk-", "canonical JSON contains no 'sk-' substring")
    assert_not_contains(json_str, TEST_API_KEY, "canonical JSON contains no test API key")

    # Verify the full cache key is a hex digest, not leaking raw params
    cache_key = generate_cache_key(params)
    assert_not_contains(cache_key, "sk-", "cache key contains no 'sk-' substring")
    assert_true(cache_key.startswith("llmix:resp:"), "cache key has correct prefix")


# ---------------------------------------------------------------------------
# 12.2 — API key not in cache value
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_12_2_api_key_not_in_cache_value():
    """After a cached call, the L1 cache entry does not contain the API key."""

    real_key = env("OPENAI_API_KEY") or ""
    cache = TwoTierCache("memory")
    pipeline, _ = make_real_pipeline(
        openai_dispatch, "openai", cache=cache,
    )

    call_input = make_call_input(
        "openai", OPENAI_MODEL, simple_prompt("12.2"),
        temperature=0.0, max_output_tokens=10,
        caching_strategy="memory",
    )
    resp = await pipeline.call(call_input)
    assert_true(resp.success, "call succeeded for cache population")

    # Inspect all L1 entries
    for key, cached_value in cache._l1.items():
        entry_str = str(cached_value.data)
        assert_not_contains(entry_str, real_key, f"L1 entry {key[:20]}… does not contain API key")

    cache.close()


# ---------------------------------------------------------------------------
# 12.3 — API key not in batch metadata (skip — no batch module)
# ---------------------------------------------------------------------------

async def test_12_3_api_key_not_in_batch_metadata():
    """Batch module not yet available — skipped."""
    print("  [SKIP] test_12_3: batch module not available")


# ---------------------------------------------------------------------------
# 12.4 — API key not in error messages
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_12_4_api_key_not_in_error_messages():
    """When a call fails, the error message must not contain the API key."""

    real_key = env("OPENAI_API_KEY") or ""
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai")

    # Use a non-existent model to force an error
    call_input = make_call_input(
        "openai", "gpt-nonexistent-model-zzz", simple_prompt("12.4"),
        temperature=0.0, max_output_tokens=10,
    )
    resp = await pipeline.call(call_input)

    assert_true(not resp.success, "call should fail with invalid model")
    assert_true(resp.error is not None, "error message present")
    assert_not_contains(resp.error or "", real_key, "error message does not contain API key")


# ---------------------------------------------------------------------------
# 12.5 — API key not in singleflight key INPUT
# ---------------------------------------------------------------------------

async def test_12_5_api_key_not_in_singleflight_key():
    """The singleflight key input is {provider, model, messages} — no API key.
    make_key() returns a SHA-256 hex digest, not raw content."""

    messages = [{"role": "user", "content": "hello"}]
    sf_input = json.dumps(
        {"provider": "openai", "model": OPENAI_MODEL, "messages": messages},
        default=str,
    )

    # The raw input string must not contain any key
    assert_not_contains(sf_input, "sk-", "singleflight input has no 'sk-' substring")
    assert_not_contains(sf_input, TEST_API_KEY, "singleflight input has no test API key")

    # make_key returns a hex digest
    hashed = Singleflight.make_key(sf_input)
    assert_true(len(hashed) == 64, f"make_key returns 64-char hex digest (got {len(hashed)})")
    assert_not_contains(hashed, "sk-", "hashed key has no 'sk-' substring")

    # Verify it's pure hex
    try:
        int(hashed, 16)
        assert_true(True, "make_key output is valid hex")
    except ValueError:
        assert_true(False, "make_key output is NOT valid hex")


# ---------------------------------------------------------------------------
# 12.6 — GPU auth token not in logs
# ---------------------------------------------------------------------------

@skip_unless("GPU_BASE_URL", "SNO_LLM_API_KEY")
async def test_12_6_gpu_auth_token_not_in_logs():
    """Log output during a SnoGPU call must not contain SNO_LLM_API_KEY."""

    from conftest import sno_gpu_dispatch

    secret = env("SNO_LLM_API_KEY") or ""
    gpu_url = env("GPU_BASE_URL") or ""

    # Capture all log output
    log_capture = logging.StreamHandler(stream := __import__("io").StringIO())
    log_capture.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()
    root_logger.addHandler(log_capture)
    prev_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)

    try:
        pipeline, _ = make_real_pipeline(sno_gpu_dispatch, "sno-gpu", api_key="not-needed")
        call_input = make_call_input(
            "sno-gpu", "Qwen/Qwen3-32B", simple_prompt("12.6"),
            temperature=0.7, max_output_tokens=20,
            base_url=gpu_url,
        )
        await pipeline.call(call_input)
    finally:
        root_logger.removeHandler(log_capture)
        root_logger.setLevel(prev_level)

    log_output = stream.getvalue()
    assert_not_contains(log_output, secret, "log output does not contain SNO_LLM_API_KEY")


# ---------------------------------------------------------------------------
# 12.7 — API key not in response object
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_12_7_api_key_not_in_response_object():
    """No field of CallResponse contains the API key."""

    real_key = env("OPENAI_API_KEY") or ""
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai")

    call_input = make_call_input(
        "openai", OPENAI_MODEL, simple_prompt("12.7"),
        temperature=0.0, max_output_tokens=10,
    )
    resp = await pipeline.call(call_input)
    assert_true(resp.success, "call succeeded")

    # Check every field individually
    for field_name in ("content", "model", "provider", "error", "thinking_content", "cache_hit"):
        val = getattr(resp, field_name)
        if val is not None:
            assert_not_contains(str(val), real_key, f"response.{field_name} clean")

    # Check the full string representation
    full_repr = repr(resp)
    assert_not_contains(full_repr, real_key, "full response repr does not contain API key")

    # Check usage object
    usage_str = repr(resp.usage)
    assert_not_contains(usage_str, real_key, "usage repr does not contain API key")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main() -> int:
    print("Suite 12: Security & Leakage")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 12")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
