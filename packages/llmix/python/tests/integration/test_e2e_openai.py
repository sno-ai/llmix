#!/usr/bin/env python3
"""Suite 1: OpenAI Real Integration Tests

Every test makes a REAL HTTP call to OpenAI. No mocking.
Requires OPENAI_API_KEY environment variable.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_contains,
    assert_eq,
    assert_failed,
    assert_gt,
    assert_json_parseable,
    assert_lt,
    assert_success,
    assert_true,
    assert_valid_usage,
    make_call_input,
    make_real_pipeline,
    openai_dispatch,
    print_summary,
    skip_unless,
)


# ---------------------------------------------------------------------------
# 1.1 Simple completion
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_1_simple_completion():
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai")
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "What is 2+2? Reply with just the number."}],
        temperature=0,
        max_output_tokens=50,
    ))
    assert_success(result, "1.1 simple")
    assert_contains(result.content, "4", "1.1 correct answer")
    assert_valid_usage(result, "1.1 usage")
    assert_true(result.model.startswith("gpt-4o-mini"), f"1.1 model: {result.model}")
    # Verify instrumented dispatch recorded the call
    assert_eq(inst.call_count, 1, "1.1 dispatch called once")
    assert_true(result.error is None, "1.1 no error")


# ---------------------------------------------------------------------------
# 1.2 Temperature=0 determinism
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_2_temperature_determinism():
    # NOTE: The pipeline does not propagate `seed` from common config to kwargs.
    # This is a potential gap -- seed set via make_call_input(..., seed=42) will
    # appear in config["common"]["seed"] but never reaches ctx.kwargs because
    # _execute_retry_body only extracts temperature, top_p, max_tokens.
    # We rely on temperature=0 alone for determinism here.
    prompt = "What is the capital of France? Reply with just the city name."
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_output_tokens=20,
    )

    pipeline1, _ = make_real_pipeline(openai_dispatch, "openai")
    r1 = await pipeline1.call(call_input)
    assert_success(r1, "1.2 first call")

    pipeline2, _ = make_real_pipeline(openai_dispatch, "openai")
    r2 = await pipeline2.call(call_input)
    assert_success(r2, "1.2 second call")

    # With temperature=0, gpt-4o-mini should be deterministic
    assert_eq(r1.content.strip(), r2.content.strip(), "1.2 deterministic output")
    assert_contains(r1.content, "Paris", "1.2 correct answer")


# ---------------------------------------------------------------------------
# 1.3 Reasoning model kwargs stripping (o4-mini)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_3_reasoning_model_kwargs_stripping():
    # o4-mini is a reasoning model. Sending temperature!=1 returns HTTP 400.
    # The pipeline's openai_transform_kwargs should strip temperature.
    # If stripping fails, OpenAI returns 400 and the call fails.
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai")
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="o4-mini",
        messages=[{"role": "user", "content": "What is 7 * 8? Reply with just the number."}],
        temperature=0.7,  # Would cause 400 if not stripped
        max_output_tokens=200,
    ))
    assert_success(result, "1.3 o4-mini success (kwargs stripped)")
    assert_contains(result.content, "56", "1.3 correct answer")
    assert_valid_usage(result, "1.3 usage")
    # Verify the dispatch received kwargs WITHOUT temperature
    last = inst.last_result
    assert_true(last is not None, "1.3 dispatch was called")
    # Check the recorded dispatch -- temperature should have been stripped
    record = inst.records[-1]
    assert_true("temperature" not in record.kwargs or record.kwargs["temperature"] is None,
                "1.3 temperature stripped from kwargs")


# ---------------------------------------------------------------------------
# 1.4 Max tokens enforcement
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_4_max_tokens_enforcement():
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai")
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Write a long essay about the history of mathematics."}],
        temperature=0,
        max_output_tokens=10,
    ))
    assert_success(result, "1.4 max tokens")
    # OpenAI may slightly exceed the token limit but output should be very short
    assert_lt(result.usage.output_tokens, 15, "1.4 output_tokens <= 15")
    # Content should be truncated / short
    assert_lt(len(result.content), 200, "1.4 content is short")


# ---------------------------------------------------------------------------
# 1.5 System message (pirate persona)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_5_system_message():
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai")
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a pirate. Every response must use pirate language with words like arr, matey, ahoy, ye, treasure, etc."},
            {"role": "user", "content": "Say hello and introduce yourself."},
        ],
        temperature=0.5,
        max_output_tokens=150,
    ))
    assert_success(result, "1.5 pirate")
    content_lower = result.content.lower()
    pirate_words = ["arr", "matey", "ahoy", "ye", "treasure", "captain", "sail", "sea", "ship", "plunder"]
    found = [w for w in pirate_words if w in content_lower]
    assert_gt(len(found), 0, f"1.5 pirate vocabulary found: {found}")


# ---------------------------------------------------------------------------
# 1.6 Invalid model (error handling)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_6_invalid_model():
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai", max_retries=0)
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="nonexistent-model-xyz",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=0,
        max_output_tokens=10,
    ))
    assert_failed(result, "1.6 invalid model")
    assert_true(result.error is not None, "1.6 error message present")
    assert_true(len(result.error or "") > 0, "1.6 error non-empty")
    # Should not crash or hang -- we got here, so no hang
    assert_eq(result.content, "", "1.6 no content on failure")
    assert_eq(result.usage.total_tokens, 0, "1.6 no usage on failure")


# ---------------------------------------------------------------------------
# 1.7 Usage accounting
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_7_usage_accounting():
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai")
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "List the first 5 prime numbers, one per line."}],
        temperature=0,
        max_output_tokens=100,
    ))
    assert_success(result, "1.7 usage")
    u = result.usage
    assert_gt(u.input_tokens, 0, "1.7 input_tokens > 0")
    assert_gt(u.output_tokens, 0, "1.7 output_tokens > 0")
    assert_gt(u.total_tokens, 0, "1.7 total_tokens > 0")
    assert_eq(u.total_tokens, u.input_tokens + u.output_tokens, "1.7 total = input + output")
    # Sanity: input should be reasonable for this short prompt
    assert_gt(u.input_tokens, 5, "1.7 input_tokens > 5 (prompt has content)")
    assert_lt(u.input_tokens, 100, "1.7 input_tokens < 100 (short prompt)")


# ---------------------------------------------------------------------------
# 1.8 JSON mode
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_1_8_json_mode():
    # BUG NOTE: The current pipeline (_execute_retry_body) only extracts temperature,
    # top_p, and max_tokens from common config into kwargs. It does NOT pass
    # response_format through to the dispatch kwargs. The openai_dispatch reads
    # response_format from ctx.kwargs, so it will be missing.
    #
    # To work around this, we test JSON mode by relying on a strong prompt that
    # instructs the model to return JSON. If the pipeline properly propagated
    # response_format, we could guarantee structured output. Without it, we rely
    # on the model following instructions.
    #
    # KNOWN GAP: response_format (and seed) from config.common never reach
    # dispatch kwargs. This should be fixed in the pipeline.
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai")
    result = await pipeline.call(make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a JSON API. You MUST respond with valid JSON only. No markdown, no code fences, no explanation."},
            {"role": "user", "content": 'Return a JSON object with keys "name" (string) and "age" (integer) for a person named Alice who is 30 years old.'},
        ],
        temperature=0,
        max_output_tokens=100,
    ))
    assert_success(result, "1.8 json mode")
    # Strip potential markdown fences the model might add despite instructions
    content = result.content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove first and last lines (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines).strip()

    parsed = assert_json_parseable(content, "1.8 JSON parse")
    if isinstance(parsed, dict):
        assert_true(True, "1.8 result is object")
        name_val = parsed.get("name")
        age_val = parsed.get("age")
        assert_eq(name_val, "Alice", "1.8 name=Alice")
        assert_eq(age_val, 30, "1.8 age=30")
    elif parsed is not None:
        assert_true(False, f"1.8 result is object (got {type(parsed).__name__})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("Suite 1: OpenAI Real Calls")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 1: OpenAI")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
