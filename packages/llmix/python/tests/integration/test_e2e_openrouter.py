#!/usr/bin/env python3
"""Suite 5C: OpenRouter Real Integration Tests

Every test makes a REAL HTTP call to OpenRouter's OpenAI-compatible API
through the shipped ``llmix.openrouter_dispatch()`` factory. No mocking.
Requires OPENROUTER_API_KEY environment variable.

Tests cover:
  - Basic completion via deepseek/deepseek-v4-flash
  - "deepseek-v4-flash" alias routing (_DEEPSEEK_MODEL_MAPPINGS)
  - Direct provider-prefixed model (deepseek/deepseek-v4-flash)
  - Token usage tracking consistency
  - Base URL correctness (https://openrouter.ai/api/v1)
  - Invalid model handled gracefully (error surfaced, not crashed)

Run:
  doppler run --config dev -- uv run python tests/integration/test_e2e_openrouter.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import (
    assert_contains,
    assert_eq,
    assert_gt,
    assert_success,
    assert_true,
    assert_valid_usage,
    make_call_input,
    make_real_pipeline,
    print_summary,
    skip_unless,
)
from llmix import openrouter_dispatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_ALIAS = "deepseek-v4-flash"


def _make_openrouter_pipeline(*, max_retries: int = 2):
    return make_real_pipeline(openrouter_dispatch(), "deepseek", api_key=os.environ["OPENROUTER_API_KEY"], max_retries=max_retries)


def _openrouter_call_input(
    *,
    model: str = OPENROUTER_MODEL,
    messages: list[dict[str, str]] | None = None,
    temperature: float | None = 0,
    max_output_tokens: int = 40,
):
    """Build a CallInput pre-configured for OpenRouter (provider=deepseek)."""
    if messages is None:
        messages = [{"role": "user", "content": "What is 2+2? Reply with just the number."}]
    # openrouter_dispatch tags itself via _mark_bypass(..., "deepseek"), so the
    # pipeline provider is "deepseek" — see dispatchers/__init__.py:391.
    return make_call_input(
        provider="deepseek",
        model=model,
        messages=messages,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


# ---------------------------------------------------------------------------
# 5C.1 Simple completion — deepseek/deepseek-v4-flash via OpenRouter
# ---------------------------------------------------------------------------

@skip_unless("OPENROUTER_API_KEY")
async def test_5c_1_simple_completion():
    pipeline, inst = _make_openrouter_pipeline()
    result = await pipeline.call(_openrouter_call_input(
        messages=[{"role": "user", "content": "What is the capital of France? Reply with just the city name."}],
        temperature=0,
        max_output_tokens=24,
    ))
    assert_success(result, "5C.1 simple completion")
    assert_contains(result.content.lower(), "paris", "5C.1 correct answer")
    assert_valid_usage(result, "5C.1 usage")
    assert_eq(inst.call_count, 1, "5C.1 dispatch called once")
    assert_true(len(result.model) > 0, "5C.1 model field populated")


# ---------------------------------------------------------------------------
# 5C.2 DeepSeek V4 Flash alias routing — "deepseek-v4-flash" → "deepseek/deepseek-v4-flash"
# ---------------------------------------------------------------------------

@skip_unless("OPENROUTER_API_KEY")
async def test_5c_2_deepseek_v4_flash_alias_routing():
    # The dispatcher maps aliases through _DEEPSEEK_MODEL_MAPPINGS.
    # Passing "deepseek-v4-flash" should resolve to
    # "deepseek/deepseek-v4-flash" before the HTTP call.
    pipeline, inst = _make_openrouter_pipeline()
    result = await pipeline.call(_openrouter_call_input(
        model=OPENROUTER_ALIAS,
        messages=[{"role": "user", "content": "Say 'ok'. Just those two letters."}],
        temperature=0,
        max_output_tokens=20,
    ))
    assert_success(result, "5C.2 legacy alias call")
    assert_eq(inst.call_count, 1, "5C.2 dispatch called once")
    # The resolved model returned by OpenRouter should reference deepseek.
    assert_contains(result.model.lower(), "deepseek", "5C.2 alias resolved to deepseek model")
    assert_valid_usage(result, "5C.2 usage")


# ---------------------------------------------------------------------------
# 5C.3 Direct provider-prefixed model accepted
# ---------------------------------------------------------------------------

@skip_unless("OPENROUTER_API_KEY")
async def test_5c_3_direct_prefixed_model():
    pipeline, inst = _make_openrouter_pipeline()
    result = await pipeline.call(_openrouter_call_input(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": "Say 'pong'. Just that one word."}],
        temperature=0,
        max_output_tokens=20,
    ))
    assert_success(result, "5C.3 direct prefixed-model call")
    assert_eq(inst.call_count, 1, "5C.3 dispatch called once")
    assert_true(
        result.model.startswith("deepseek/"),
        f"5C.3 returned model starts with 'deepseek/': {result.model!r}",
    )


# ---------------------------------------------------------------------------
# 5C.4 Token usage tracking
# ---------------------------------------------------------------------------

@skip_unless("OPENROUTER_API_KEY")
async def test_5c_4_token_usage_tracking():
    pipeline, _ = _make_openrouter_pipeline()
    result = await pipeline.call(_openrouter_call_input(
        messages=[{"role": "user", "content": "List 3 primary colors, comma-separated."}],
        temperature=0,
        max_output_tokens=40,
    ))
    assert_success(result, "5C.4 usage-tracking call")
    usage = result.usage
    assert_gt(usage.input_tokens, 0, "5C.4 input_tokens > 0")
    assert_gt(usage.output_tokens, 0, "5C.4 output_tokens > 0")
    # OpenRouter reports total = input + output (no internal thinking tokens
    # for DeepSeek V4 Flash).
    assert_eq(
        usage.total_tokens,
        usage.input_tokens + usage.output_tokens,
        "5C.4 total_tokens == input + output",
    )


# ---------------------------------------------------------------------------
# 5C.5 Base URL correctness — https://openrouter.ai/api/v1
# ---------------------------------------------------------------------------

@skip_unless("OPENROUTER_API_KEY")
async def test_5c_5_base_url_correct():
    # openrouter_dispatch hardcodes base_url to OPENROUTER_BASE_URL
    # (https://openrouter.ai/api/v1). We verify by making a successful call —
    # if the URL were wrong, the call would fail with a 404 / connection error.
    pipeline, inst = _make_openrouter_pipeline()
    result = await pipeline.call(_openrouter_call_input(
        messages=[{"role": "user", "content": "Say 'ok'. Just those two letters."}],
        temperature=0,
        max_output_tokens=20,
    ))
    assert_success(result, "5C.5 base URL works")
    assert_eq(inst.call_count, 1, "5C.5 dispatch called once")
    assert_true(result.error is None, "5C.5 no connection error (URL is correct)")


# ---------------------------------------------------------------------------
# 5C.6 Invalid model handled gracefully
# ---------------------------------------------------------------------------

@skip_unless("OPENROUTER_API_KEY")
async def test_5c_6_invalid_model():
    # Pass a nonsense model ID. OpenRouter returns a 4xx error, which the
    # pipeline should surface via result.success=False + result.error non-empty
    # (mirrors test_1_6 pattern for OpenAI).
    pipeline, _ = _make_openrouter_pipeline(max_retries=0)
    result = await pipeline.call(_openrouter_call_input(
        model="invalid/nonexistent-xyz-123",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=0,
        max_output_tokens=10,
    ))
    assert_true(not result.success, "5C.6 success=False on invalid model")
    assert_true(result.error is not None and len(result.error) > 0, "5C.6 error message present")
    assert_eq(result.content, "", "5C.6 no content on failure")
    assert_eq(result.usage.total_tokens, 0, "5C.6 no usage on failure")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("Suite 5C: OpenRouter Real Calls")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 5C: OpenRouter")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
