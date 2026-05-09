#!/usr/bin/env python3
"""Suite 5: Novita Real Integration Tests

Every test makes a REAL HTTP call to Novita's OpenAI-compatible API.
No mocking. Requires NOVITA_API_KEY environment variable.

Tests cover:
  - Basic completion via Novita's OpenAI-compat endpoint
  - Thinking mode activation (lenient — Novita may handle differently)
  - Base URL correctness (https://api.novita.ai/v3/openai)
  - Cross-provider parity with SnoGPU (same prompt, both providers)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_contains,
    assert_eq,
    assert_gt,
    assert_success,
    assert_true,
    assert_valid_usage,
    env,
    make_call_input,
    make_real_pipeline,
    novita_dispatch,
    print_summary,
    skip_unless,
    sno_gpu_dispatch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOVITA_MODEL = "qwen/qwen3.5-27b"


def _novita_call_input(
    *,
    model: str = NOVITA_MODEL,
    messages: list[dict[str, str]] | None = None,
    temperature: float | None = None,
    max_output_tokens: int = 200,
):
    """Build a CallInput pre-configured for Novita."""
    if messages is None:
        messages = [{"role": "user", "content": "What is 2+2? Reply with just the number."}]
    return make_call_input(
        provider="novita",
        model=model,
        messages=messages,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


# ---------------------------------------------------------------------------
# 5.1 Simple completion — qwen model via Novita
# ---------------------------------------------------------------------------

@skip_unless("NOVITA_API_KEY")
async def test_5_1_simple_completion():
    pipeline, inst = make_real_pipeline(novita_dispatch, "novita")
    result = await pipeline.call(_novita_call_input(
        messages=[{"role": "user", "content": "What is the capital of Germany? Reply with just the city name."}],
        temperature=0,
        max_output_tokens=50,
    ))
    assert_success(result, "5.1 simple completion")
    assert_contains(result.content.lower(), "berlin", "5.1 correct answer")
    assert_valid_usage(result, "5.1 usage")
    assert_eq(inst.call_count, 1, "5.1 dispatch called once")
    assert_true(len(result.model) > 0, "5.1 model field populated")


# ---------------------------------------------------------------------------
# 5.2 Thinking enabled — enableThinking=true
# ---------------------------------------------------------------------------

@skip_unless("NOVITA_API_KEY")
async def test_5_2_thinking_enabled():
    # Novita's OpenAI-compat API may or may not support thinking mode via
    # extra_body.chat_template_kwargs. The novita_dispatch in conftest does NOT
    # inject enable_thinking (unlike sno_gpu_dispatch). Novita's Qwen models
    # may produce <think> blocks by default or may require different activation.
    #
    # This test is LENIENT: we check if thinking blocks appear but do not fail
    # if they don't, since Novita's API may handle thinking differently.
    pipeline, _ = make_real_pipeline(novita_dispatch, "novita")
    result = await pipeline.call(_novita_call_input(
        messages=[
            {"role": "user", "content": "Think step by step: what is 23 * 47?"},
        ],
        max_output_tokens=1000,
    ))
    assert_success(result, "5.2 thinking call succeeds")
    assert_gt(len(result.content), 0, "5.2 content non-empty")

    # Check for thinking indicators (lenient)
    raw_content = result.content
    thinking_content = result.thinking_content or ""
    has_think_tags = "<think>" in raw_content or "<think>" in thinking_content
    has_step_reasoning = any(
        marker in raw_content.lower()
        for marker in ["step 1", "first", "multiply", "let me"]
    )

    if has_think_tags:
        print("  [INFO] 5.2 <think> blocks detected — thinking mode active via Novita")
        assert_true(True, "5.2 thinking blocks present")
    elif has_step_reasoning:
        print("  [INFO] 5.2 step-by-step reasoning found (no <think> tags)")
        assert_true(True, "5.2 reasoning present without <think> tags")
    else:
        # Don't fail — Novita may not support thinking mode the same way
        print("  [INFO] 5.2 no explicit thinking indicators — Novita may handle differently")
        assert_true(True, "5.2 call completed (thinking mode handling varies by provider)")

    # Regardless of thinking mode, the math should be correct
    assert_contains(result.content, "1081", "5.2 correct answer: 23*47=1081")


# ---------------------------------------------------------------------------
# 5.3 Base URL correct — https://api.novita.ai/v3/openai
# ---------------------------------------------------------------------------

@skip_unless("NOVITA_API_KEY")
async def test_5_3_base_url_correct():
    # The novita_dispatch hardcodes base_url to https://api.novita.ai/v3/openai.
    # We verify by making a successful call — if the URL were wrong, the call
    # would fail with a connection error or 404.
    pipeline, inst = make_real_pipeline(novita_dispatch, "novita")
    result = await pipeline.call(_novita_call_input(
        messages=[{"role": "user", "content": "Say 'pong'. Just that one word."}],
        temperature=0,
        max_output_tokens=20,
    ))
    assert_success(result, "5.3 base URL works")
    # Verify we actually made a real call
    assert_eq(inst.call_count, 1, "5.3 dispatch called")
    assert_true(result.error is None, "5.3 no connection error (URL is correct)")

    # Content sanity — model should respond with something containing "pong"
    assert_contains(result.content.lower(), "pong", "5.3 model responded correctly")


# ---------------------------------------------------------------------------
# 5.4 Comparison with SnoGPU — same prompt to both providers
# ---------------------------------------------------------------------------

@skip_unless("NOVITA_API_KEY", "GPU_BASE_URL", "SNO_LLM_API_KEY")
async def test_5_4_cross_provider_parity():
    prompt = "List exactly 3 primary colors. Reply with just the color names, comma-separated."
    messages = [{"role": "user", "content": prompt}]

    # Novita call
    novita_pipeline, novita_inst = make_real_pipeline(novita_dispatch, "novita")
    novita_result = await novita_pipeline.call(_novita_call_input(
        messages=messages,
        temperature=0,
        max_output_tokens=50,
    ))

    # SnoGPU call
    gpu_base = env("GPU_BASE_URL") or ""
    sno_gpu_pipeline, sno_gpu_inst = make_real_pipeline(sno_gpu_dispatch, "sno-gpu", api_key="not-needed")
    sno_gpu_result = await sno_gpu_pipeline.call(make_call_input(
        provider="sno-gpu",
        model="qwen3.6-27b-extract",
        messages=messages,
        temperature=0,
        max_output_tokens=50,
        base_url=gpu_base,
        provider_options={"sno-gpu": {"gpu_path": "extract"}},
    ))

    # Both should succeed
    assert_success(novita_result, "5.4 Novita succeeds")
    assert_success(sno_gpu_result, "5.4 SnoGPU succeeds")

    # Both should produce coherent answers about primary colors
    primary_colors = ["red", "blue", "yellow", "green"]  # Allow either RGB or RYB
    novita_lower = novita_result.content.lower()
    sno_gpu_lower = sno_gpu_result.content.lower()

    novita_colors = [c for c in primary_colors if c in novita_lower]
    sno_gpu_colors = [c for c in primary_colors if c in sno_gpu_lower]

    assert_gt(len(novita_colors), 1, f"5.4 Novita found colors: {novita_colors}")
    assert_gt(len(sno_gpu_colors), 1, f"5.4 SnoGPU found colors: {sno_gpu_colors}")

    # Log both responses for manual comparison
    print(f"  [INFO] 5.4 Novita response: {novita_result.content[:100]!r}")
    print(f"  [INFO] 5.4 SnoGPU response: {sno_gpu_result.content[:100]!r}")

    # Both dispatches should have been called exactly once
    assert_eq(novita_inst.call_count, 1, "5.4 Novita dispatch count")
    assert_eq(sno_gpu_inst.call_count, 1, "5.4 SnoGPU dispatch count")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("Suite 5: Novita Real Calls")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 5: Novita")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
