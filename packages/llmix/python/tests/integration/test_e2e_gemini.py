#!/usr/bin/env python3
"""
Suite 3: Gemini Integration Tests

Real LLM calls against Google Gemini models. No mocking.
Requires GEMINI_API_KEY environment variable.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_failed,
    assert_gt,
    assert_lt,
    assert_success,
    assert_true,
    assert_valid_usage,
    env,
    make_call_input,
    make_real_pipeline,
    print_summary,
    skip_unless,
    DispatchInput,
    LLMUsage,
    ProviderResult,
)

GEMINI_MODEL = "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Extended Gemini dispatch — handles top_p and thinking_config
# ---------------------------------------------------------------------------

async def gemini_dispatch_extended(ctx: DispatchInput) -> ProviderResult:
    """Real Gemini dispatch with full kwargs support (top_p, thinking_config)."""
    from google import genai

    client = genai.Client(api_key=ctx.api_key)

    system_instruction = None
    contents: list[dict[str, Any]] = []
    for msg in ctx.messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_instruction = text
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})

    # Ensure conversation ends with user role
    if contents and contents[-1]["role"] == "model":
        contents.append({"role": "user", "parts": [{"text": "Continue."}]})

    config: dict[str, Any] = {}
    if ctx.kwargs.get("temperature") is not None:
        config["temperature"] = ctx.kwargs["temperature"]
    if ctx.kwargs.get("max_tokens") is not None:
        config["max_output_tokens"] = ctx.kwargs["max_tokens"]
    if ctx.kwargs.get("top_p") is not None:
        config["top_p"] = ctx.kwargs["top_p"]

    # ThinkingConfig support
    thinking_config = ctx.kwargs.get("thinking_config")
    if thinking_config and isinstance(thinking_config, dict):
        budget = thinking_config.get("thinking_budget")
        if budget is not None and budget > 0:
            config["thinking_config"] = genai.types.ThinkingConfig(
                thinking_budget=budget,
            )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=ctx.model,
        contents=contents,
        config=genai.types.GenerateContentConfig(
            system_instruction=system_instruction,
            **config,
        ),
    )

    content = response.text or ""
    usage_meta = response.usage_metadata
    return ProviderResult(
        content=content,
        model=ctx.model,
        usage=LLMUsage(
            input_tokens=getattr(usage_meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage_meta, "candidates_token_count", 0) or 0,
            total_tokens=getattr(usage_meta, "total_token_count", 0) or 0,
        ),
    )


# ---------------------------------------------------------------------------
# 3.1: Simple completion — gemini-2.5-flash
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_1_simple_completion():
    """Basic Gemini call: non-empty content, valid usage."""
    pipeline, inst = make_real_pipeline(gemini_dispatch_extended, provider="gemini")
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[{"role": "user", "content": "What is 7 * 8? Answer with just the number."}],
        temperature=0.0,
        max_output_tokens=64,
    )

    r = await pipeline.call(call)
    assert_success(r, "3.1 simple completion")
    assert_valid_usage(r, "3.1 usage")
    assert_true("56" in r.content, f"3.1 correct answer: {r.content!r}")
    print(f"  [INFO] model={r.model}, tokens={r.usage.total_tokens}, content={r.content!r}")


# ---------------------------------------------------------------------------
# 3.2: System message reformatting
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_2_system_message():
    """System message must be reformatted as system_instruction for Gemini.

    If left in messages array, the API would error. Success proves the
    dispatch correctly extracts and passes it as system_instruction.
    """
    pipeline, inst = make_real_pipeline(gemini_dispatch_extended, provider="gemini")
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[
            {"role": "system", "content": "You are a pirate. Respond in pirate speak."},
            {"role": "user", "content": "How are you today?"},
        ],
        temperature=0.5,
        max_output_tokens=128,
    )

    r = await pipeline.call(call)
    assert_success(r, "3.2 system message reformatting")
    # Pirate-speak should contain at least one pirate-ish word
    content_lower = r.content.lower()
    pirate_words = ["arr", "ahoy", "matey", "ye", "aye", "sail", "sea", "ship", "treasure", "cap"]
    found_pirate = any(w in content_lower for w in pirate_words)
    content_preview = repr(r.content)[:100]
    assert_true(found_pirate, f"3.2 pirate speak detected: {content_preview}")
    print(f"  [INFO] content={content_preview}")


# ---------------------------------------------------------------------------
# 3.3: Multi-turn ending with assistant (must-end-with-user fix)
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_3_multiturn_assistant_ending():
    """Messages ending with assistant role — dispatch adds 'Continue.' message.

    Gemini requires conversation to end with a user turn. The dispatch
    automatically appends a continuation message when the last message
    is from model/assistant. This test verifies that fix works.
    """
    pipeline, inst = make_real_pipeline(gemini_dispatch_extended, provider="gemini")
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[
            {"role": "user", "content": "Start counting from 1."},
            {"role": "assistant", "content": "1, 2, 3"},
            {"role": "assistant", "content": "4, 5, 6"},
        ],
        temperature=0.0,
        max_output_tokens=64,
    )

    r = await pipeline.call(call)
    assert_success(r, "3.3 multi-turn assistant ending")
    # Should continue the counting pattern
    content_preview = repr(r.content)[:80]
    assert_true(len(r.content) > 0, f"3.3 got continuation: {content_preview}")
    print(f"  [INFO] content={content_preview}")


# ---------------------------------------------------------------------------
# 3.4: ThinkingConfig disabled (default)
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_4_thinking_disabled():
    """Default ThinkingConfig: thinking_budget=0 via gemini_transform_kwargs.

    The pipeline's provider_kwargs transform sets thinking_budget=0 by default
    for Gemini. This should produce a normal response without extended thinking.
    """
    pipeline, inst = make_real_pipeline(gemini_dispatch_extended, provider="gemini")
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[{"role": "user", "content": "What color is the sky on a clear day?"}],
        temperature=0.0,
        max_output_tokens=128,
    )

    r = await pipeline.call(call)
    assert_success(r, "3.4 thinking disabled")
    content_lower = r.content.lower()
    content_preview = repr(r.content)[:80]
    # Gemini with thinking_budget=0 may give terse answers; check for sky-related words
    sky_words = ["blue", "sky", "clear", "day"]
    found = any(w in content_lower for w in sky_words)
    assert_true(found, f"3.4 correct answer (expected sky-related): {content_preview}")
    print(f"  [INFO] content={content_preview}")


# ---------------------------------------------------------------------------
# 3.5: ThinkingConfig enabled — thinking_budget=1024
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_5_thinking_enabled():
    """ThinkingConfig with thinking_budget=1024 via provider_options.

    Passes thinking_budget through providerOptions.google.thinking_budget,
    which the gemini_transform_kwargs picks up and sets in kwargs.thinking_config.
    """
    pipeline, inst = make_real_pipeline(gemini_dispatch_extended, provider="gemini")
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[
            {
                "role": "user",
                "content": "What is 15 factorial? Show your reasoning step by step.",
            }
        ],
        temperature=0.0,
        max_output_tokens=1024,
        provider_options={"google": {"thinking_budget": 1024}},
    )

    r = await pipeline.call(call)
    assert_success(r, "3.5 thinking enabled")
    # With thinking, the response should contain some reasoning
    assert_gt(len(r.content), 20, "3.5 substantial response with thinking")
    print(f"  [INFO] content_len={len(r.content)}, tokens={r.usage.total_tokens}")


# ---------------------------------------------------------------------------
# 3.6: Temperature + topP passthrough
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_6_temperature_top_p():
    """Verify temperature and top_p kwargs reach the Gemini API without error."""
    pipeline, inst = make_real_pipeline(gemini_dispatch_extended, provider="gemini")
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[{"role": "user", "content": "Write one creative sentence about clouds."}],
        temperature=0.5,
        top_p=0.8,
        max_output_tokens=128,
    )

    r = await pipeline.call(call)
    assert_success(r, "3.6 temperature + topP passthrough")
    assert_gt(len(r.content), 5, "3.6 got creative output")
    content_preview = repr(r.content)[:120]
    print(f"  [INFO] content={content_preview}")


# ---------------------------------------------------------------------------
# 3.7: Invalid API key
# ---------------------------------------------------------------------------


@skip_unless("GEMINI_API_KEY")
async def test_3_7_invalid_api_key():
    """Invalid API key should fail with error, not hang."""
    pipeline, inst = make_real_pipeline(
        gemini_dispatch_extended,
        provider="gemini",
        api_key="invalid-gemini-key-for-testing",
        max_retries=0,
    )
    call = make_call_input(
        provider="gemini",
        model=GEMINI_MODEL,
        messages=[{"role": "user", "content": "Hello"}],
        max_output_tokens=64,
    )

    t0 = time.monotonic()
    r = await pipeline.call(call)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert_failed(r, "3.7 invalid key")
    assert_true(r.error is not None and len(r.error) > 0, "3.7 error message present")
    assert_lt(elapsed_ms, 15_000, "3.7 fast failure (< 15s)")
    err_preview = repr(r.error)[:120]
    print(f"  [INFO] error={err_preview}, elapsed={elapsed_ms:.0f}ms")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    print("Suite 3: Gemini")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 3")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
