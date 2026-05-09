#!/usr/bin/env python3
"""
Suite 2: Anthropic Integration Tests

Real LLM calls against Claude models. No mocking.
Requires ANTHROPIC_API_KEY environment variable.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    anthropic_dispatch,
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
)


# ---------------------------------------------------------------------------
# 2.1: Simple completion — claude-haiku-4-5-20251001
# ---------------------------------------------------------------------------


@skip_unless("ANTHROPIC_API_KEY")
async def test_2_1_simple_completion():
    """Basic Anthropic call: non-empty content, valid usage, correct model."""
    pipeline, inst = make_real_pipeline(anthropic_dispatch, provider="anthropic")
    call = make_call_input(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "What is 2 + 2? Answer with just the number."}],
        temperature=0.0,
        max_output_tokens=64,
    )

    r = await pipeline.call(call)
    assert_success(r, "2.1 simple completion")
    assert_valid_usage(r, "2.1 usage")
    assert_true(
        "claude" in r.model.lower() or "haiku" in r.model.lower(),
        f"2.1 model matches: got {r.model}",
    )
    assert_true("4" in r.content, f"2.1 content has correct answer: {r.content!r}")
    print(f"  [INFO] model={r.model}, tokens={r.usage.total_tokens}, content={r.content!r}")


# ---------------------------------------------------------------------------
# 2.2: System message extraction
# ---------------------------------------------------------------------------


@skip_unless("ANTHROPIC_API_KEY")
async def test_2_2_system_message_extraction():
    """System message must be extracted as separate param, not left in messages array.

    Anthropic API errors if system message is in the messages array.
    We also verify the model roughly follows the 3-word constraint.
    """
    pipeline, inst = make_real_pipeline(anthropic_dispatch, provider="anthropic")
    call = make_call_input(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        messages=[
            {"role": "system", "content": "You must respond in exactly 3 words."},
            {"role": "user", "content": "Describe the sky."},
        ],
        temperature=0.0,
        max_output_tokens=64,
    )

    r = await pipeline.call(call)
    assert_success(r, "2.2 system msg extraction (no API error)")
    # Count words — allow +-1 tolerance
    word_count = len(r.content.strip().split())
    assert_true(
        2 <= word_count <= 4,
        f"2.2 roughly 3 words (±1): got {word_count} words: {r.content!r}",
    )
    print(f"  [INFO] content={r.content!r}, word_count={word_count}")


# ---------------------------------------------------------------------------
# 2.3: Native prompt caching via direct SDK
# ---------------------------------------------------------------------------


@skip_unless("ANTHROPIC_API_KEY")
async def test_2_3_native_prompt_caching():
    """Test Anthropic native prompt caching via direct SDK (not the main pipeline).

    The main pipeline's caching.strategy=native is a response cache concept, not
    Anthropic's server-side prompt caching. To test native prompt caching, we
    call the Anthropic SDK directly with a large system prompt (>1024 tokens)
    marked with cache_control. The second call should show cache_read_input_tokens > 0.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=env("ANTHROPIC_API_KEY"))

    # Build a system prompt > 1024 tokens to exceed cache minimum
    large_system = (
        "You are an expert assistant. " * 200
        + "Always answer concisely."
    )

    model = "claude-haiku-4-5-20251001"
    max_tokens = 64
    system = [
        {
            "type": "text",
            "text": large_system,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    user_messages: list[dict[str, str]] = [{"role": "user", "content": "Say hello."}]

    # First call — populates cache
    r1 = await client.messages.create(  # type: ignore[arg-type]
        model=model, max_tokens=max_tokens, system=system, messages=user_messages,
    )
    assert_true(r1.usage.input_tokens > 0, "2.3 call 1: input_tokens > 0")
    cache_creation = getattr(r1.usage, "cache_creation_input_tokens", 0) or 0
    print(f"  [INFO] call 1: input={r1.usage.input_tokens}, cache_creation={cache_creation}")

    # Second call — should hit cache
    r2 = await client.messages.create(  # type: ignore[arg-type]
        model=model, max_tokens=max_tokens, system=system, messages=user_messages,
    )
    cache_read = getattr(r2.usage, "cache_read_input_tokens", 0) or 0
    assert_gt(cache_read, 0, "2.3 call 2: cache_read_input_tokens > 0")
    print(f"  [INFO] call 2: input={r2.usage.input_tokens}, cache_read={cache_read}")


# ---------------------------------------------------------------------------
# 2.4: Long output — max_tokens=2000
# ---------------------------------------------------------------------------


@skip_unless("ANTHROPIC_API_KEY")
async def test_2_4_long_output():
    """Request detailed output and verify substantial token usage and content length."""
    pipeline, inst = make_real_pipeline(anthropic_dispatch, provider="anthropic")
    call = make_call_input(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        messages=[
            {
                "role": "user",
                "content": (
                    "Write a detailed explanation of how photosynthesis works, "
                    "covering the light-dependent reactions, the Calvin cycle, "
                    "and the role of chloroplasts. Be thorough."
                ),
            }
        ],
        temperature=0.5,
        max_output_tokens=2000,
    )

    r = await pipeline.call(call)
    assert_success(r, "2.4 long output")
    assert_gt(r.usage.output_tokens, 100, "2.4 output_tokens > 100")
    assert_gt(len(r.content), 500, "2.4 content > 500 chars")
    print(f"  [INFO] output_tokens={r.usage.output_tokens}, content_len={len(r.content)}")


# ---------------------------------------------------------------------------
# 2.5: Error classification — invalid API key
# ---------------------------------------------------------------------------


@skip_unless("ANTHROPIC_API_KEY")
async def test_2_5_invalid_api_key():
    """Invalid API key should fail fast with error, not hang or retry forever."""
    pipeline, inst = make_real_pipeline(
        anthropic_dispatch,
        provider="anthropic",
        api_key="sk-ant-invalid-key-for-testing",
        max_retries=0,
    )
    call = make_call_input(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "Hello"}],
        max_output_tokens=64,
    )

    t0 = time.monotonic()
    r = await pipeline.call(call)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert_failed(r, "2.5 invalid key")
    assert_true(r.error is not None and len(r.error) > 0, "2.5 error message present")
    assert_lt(elapsed_ms, 15_000, "2.5 fast failure (< 15s)")
    err_preview = repr(r.error)[:120]
    print(f"  [INFO] error={err_preview}, elapsed={elapsed_ms:.0f}ms")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    print("Suite 2: Anthropic")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 2")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
