#!/usr/bin/env python3
"""Integration tests for LLMix Call Pipeline.

Tests the full 19-step call flow with a mock provider dispatch function.
Covers: happy path, error handling, singleflight dedup, semaphore release
on failure, circuit breaker behavior, thinking stripping, and key rotation.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Ensure the python package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.pipeline import (
    CallInput,
    CallPipeline,
    DispatchInput,
    LLMUsage,
    PipelineConfig,
    ProviderError,
    ProviderResult,
)
from llmix.key_pool import KeyPool
from llmix.response_cache import TwoTierCache

passed = 0
failed = 0


def assert_true(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def assert_eq(actual: object, expected: object, msg: str) -> None:
    if actual == expected:
        assert_true(True, msg)
    else:
        assert_true(False, f"{msg}: expected {expected!r}, got {actual!r}")


def make_config(**overrides: object) -> dict:
    base = {"provider": "openai", "model": "gpt-4", "common": {"temperature": 0.7}}
    base.update(overrides)
    return base


def make_usage() -> LLMUsage:
    return LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30)


def mock_dispatch(content: str = "Hello, world!", model: str = "gpt-4", headers: dict | None = None):
    async def _dispatch(ctx: DispatchInput) -> ProviderResult:
        return ProviderResult(content=content, model=model, usage=make_usage(), headers=headers)
    return _dispatch


def error_dispatch(status_code: int | None = None, message: str = "Provider error"):
    async def _dispatch(ctx: DispatchInput) -> ProviderResult:
        raise ProviderError(message, status_code=status_code)
    return _dispatch


def make_pipeline_config(dispatch, **overrides) -> PipelineConfig:
    defaults = dict(
        dispatch=dispatch,
        max_retries=0,
        retry_base_ms=1,
        retry_max_delay_ms=1,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def make_pipeline(dispatch, **overrides) -> CallPipeline:
    """Create a pipeline with a default key pool for testing."""
    cfg = make_pipeline_config(dispatch, **overrides)
    pipeline = CallPipeline(cfg)
    pipeline.set_key_pool("openai", KeyPool(["test-key"]))
    return pipeline


# =========================================================================
# Tests
# =========================================================================


async def test_happy_path() -> None:
    pipeline = make_pipeline(mock_dispatch())
    result = await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "hi"}],
    ))
    assert_eq(result.success, True, "happy path: success is True")
    assert_eq(result.content, "Hello, world!", "happy path: content matches")
    assert_eq(result.model, "gpt-4", "happy path: model matches")
    assert_eq(result.provider, "openai", "happy path: provider matches")
    assert_eq(result.usage.total_tokens, 30, "happy path: usage matches")
    assert_eq(result.error, None, "happy path: no error")


async def test_error_returns_failure() -> None:
    pipeline = make_pipeline(error_dispatch(500, "Server error"))
    result = await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "hi"}],
    ))
    assert_eq(result.success, False, "error: success is False")
    assert_true(result.error is not None, "error: error message present")
    assert_eq(result.content, "", "error: content is empty")


async def test_thinking_stripping() -> None:
    dispatch = mock_dispatch(content="<think>reasoning here</think>The answer is 42.")
    pipeline = make_pipeline(dispatch)
    result = await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "What?"}],
    ))
    assert_eq(result.success, True, "thinking strip: success")
    assert_eq(result.content, "The answer is 42.", "thinking strip: content stripped")
    assert_eq(result.thinking_content, "reasoning here", "thinking strip: thinking captured")


async def test_keep_thinking_output() -> None:
    dispatch = mock_dispatch(content="<think>reasoning</think>The answer.")
    pipeline = make_pipeline(dispatch)
    result = await pipeline.call(CallInput(
        config=make_config(common={"keep_thinking_output": True}),
        messages=[{"role": "user", "content": "What?"}],
    ))
    assert_eq(result.success, True, "keep thinking: success")
    assert_true("<think>" in result.content, "keep thinking: thinking blocks preserved")
    assert_eq(result.thinking_content, None, "keep thinking: no thinking_content")


async def test_singleflight_dedup() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return ProviderResult(content="ok", model="gpt-4", usage=make_usage())

    pipeline = make_pipeline(dispatch)
    config = make_config()
    messages = [{"role": "user", "content": "dedup"}]
    sf_key = "dedup-key"

    r1, r2 = await asyncio.gather(
        pipeline.call(CallInput(config=config, messages=messages, singleflight_key=sf_key)),
        pipeline.call(CallInput(config=config, messages=messages, singleflight_key=sf_key)),
    )

    assert_eq(r1.success, True, "singleflight: first succeeds")
    assert_eq(r2.success, True, "singleflight: second succeeds")
    assert_eq(call_count, 1, "singleflight: dispatch called only once")


async def test_semaphore_release_on_failure() -> None:
    pipeline = make_pipeline(error_dispatch(500))
    result = await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "fail"}],
    ))
    assert_eq(result.success, False, "semaphore release on failure: call fails")

    # If semaphore wasn't released, this would hang forever
    pipeline2 = make_pipeline(
        mock_dispatch(), semaphore_initial=1, semaphore_min=1,
    )
    r1 = await pipeline2.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "ok"}],
    ))
    assert_eq(r1.success, True, "semaphore release on failure: subsequent call succeeds")


async def test_semaphore_release_on_key_selection_failure() -> None:
    pipeline = make_pipeline(
        mock_dispatch(), semaphore_initial=1, semaphore_min=1,
    )
    exhausted_pool = KeyPool(["dead-key"])
    exhausted_pool.mark_dead("dead-key")
    pipeline.set_key_pool("openai", exhausted_pool)

    r1 = await asyncio.wait_for(
        pipeline.call(CallInput(
            config=make_config(),
            messages=[{"role": "user", "content": "no-key-1"}],
            singleflight_key="no-key-1",
        )),
        timeout=0.2,
    )
    r2 = await asyncio.wait_for(
        pipeline.call(CallInput(
            config=make_config(),
            messages=[{"role": "user", "content": "no-key-2"}],
            singleflight_key="no-key-2",
        )),
        timeout=0.2,
    )

    assert_eq(r1.success, False, "key selection failure: first call fails")
    assert_eq(r2.success, False, "key selection failure: second call also fails without hanging")
    assert_eq(pipeline.get_semaphore_window("openai"), 1, "key selection failure: semaphore permit restored")


async def test_circuit_breaker_trips() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        raise ProviderError("Server error", status_code=500)

    pipeline = make_pipeline(
        dispatch,
        max_retries=0,
        circuit_breaker_threshold=2,
        circuit_breaker_cooldown_seconds=60.0,
    )

    config = make_config()
    messages = [{"role": "user", "content": "trip"}]

    # First 2 calls trigger the breaker
    await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="t1"))
    await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="t2"))

    # Third call should be rejected by circuit breaker
    result = await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="t3"))
    assert_eq(result.success, False, "circuit breaker: third call fails")
    assert_true(
        result.error is not None and "Circuit breaker OPEN" in result.error,
        "circuit breaker: error message",
    )

    # The breaker should have prevented the third dispatch
    assert_eq(call_count, 2, "circuit breaker: only 2 dispatches")


async def test_circuit_breaker_only_counts_retryable() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        raise ProviderError("Bad request", status_code=400)

    pipeline = make_pipeline(
        dispatch,
        max_retries=0,
        circuit_breaker_threshold=2,
    )

    config = make_config()
    messages = [{"role": "user", "content": "400"}]

    # 400 errors should NOT trip the breaker
    await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="a1"))
    await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="a2"))
    await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="a3"))

    assert_eq(call_count, 3, "non-retryable: all 3 dispatches executed (breaker not tripped)")


async def test_circuit_breaker_ignores_local_validation_errors() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        raise ValueError("invalid local request")

    pipeline = make_pipeline(
        dispatch,
        max_retries=0,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown_seconds=60.0,
    )

    config = make_config()
    messages = [{"role": "user", "content": "local-error"}]

    first = await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="local-1"))
    second = await pipeline.call(CallInput(config=config, messages=messages, singleflight_key="local-2"))

    assert_eq(first.success, False, "local validation error: first call fails")
    assert_eq(second.success, False, "local validation error: second call also fails")
    assert_eq(call_count, 2, "local validation error: breaker does not block later dispatches")
    assert_eq(
        pipeline.get_circuit_breaker_state("openai", ""),
        "CLOSED",
        "local validation error: breaker stays closed",
    )


async def test_key_pool_rotation() -> None:
    used_keys: list[str] = []

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        used_keys.append(ctx.api_key)
        if len(used_keys) <= 1:
            raise ProviderError("Rate limited", status_code=429)
        return ProviderResult(content="ok", model="gpt-4", usage=make_usage())

    pipeline = make_pipeline(
        dispatch, max_retries=2, retry_base_ms=1, retry_max_delay_ms=1,
    )
    pipeline.set_key_pool("openai", KeyPool(["key-a", "key-b"]))

    result = await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "rotate"}],
    ))

    assert_eq(result.success, True, "key rotation: eventually succeeds")
    assert_eq(used_keys, ["key-a", "key-b"], "key rotation: retries advance to the next key")


async def test_kill_switch() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        ks_file = Path(tmpdir) / "killswitch"
        ks_file.touch()

        pipeline = make_pipeline(
            mock_dispatch(), kill_switch_state_dir=tmpdir,
        )

        result = await pipeline.call(CallInput(
            config=make_config(),
            messages=[{"role": "user", "content": "blocked"}],
        ))

        assert_eq(result.success, False, "kill switch: call blocked")
        assert_true(
            result.error is not None and "Kill switch active" in result.error,
            "kill switch: error message",
        )


async def test_retry_on_transient_error() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ProviderError("Transient", status_code=503)
        return ProviderResult(content="recovered", model="gpt-4", usage=make_usage())

    pipeline = make_pipeline(
        dispatch, max_retries=3, retry_base_ms=1, retry_max_delay_ms=1,
    )

    result = await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "retry"}],
    ))

    assert_eq(result.success, True, "retry: eventually succeeds")
    assert_eq(result.content, "recovered", "retry: correct content")
    assert_eq(call_count, 3, "retry: called 3 times (2 failures + 1 success)")


async def test_half_open_counts_per_admitted_execution() -> None:
    attempts: dict[str, int] = {}
    opening_call = True

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal opening_call
        request_id = str(ctx.messages[0]["content"])
        if opening_call:
            opening_call = False
            raise ProviderError("Open the breaker", status_code=503)

        attempt = attempts.get(request_id, 0) + 1
        attempts[request_id] = attempt
        if attempt == 1:
            raise ProviderError("Transient", status_code=503)
        return ProviderResult(content="recovered", model="gpt-4", usage=make_usage())

    pipeline = make_pipeline(
        dispatch,
        max_retries=1,
        retry_base_ms=1,
        retry_max_delay_ms=1,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown_seconds=0.01,
    )

    await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "open-breaker"}],
        singleflight_key="open-breaker",
    ))
    await asyncio.sleep(0.02)

    for i in range(5):
        result = await pipeline.call(CallInput(
            config=make_config(),
            messages=[{"role": "user", "content": f"recover-{i}"}],
            singleflight_key=f"recover-{i}",
        ))
        assert_eq(result.success, True, f"half-open retry accounting: recovery call {i + 1} succeeds")

    assert_eq(
        pipeline.get_circuit_breaker_state("openai", ""),
        "HALF_OPEN",
        "half-open retry accounting: breaker stays half-open until 10 admitted executions finish",
    )


async def test_half_open_counts_failed_retry_sequence_once() -> None:
    attempts: dict[str, int] = {}
    opening_call = True

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal opening_call
        request_id = str(ctx.messages[0]["content"])
        if opening_call:
            opening_call = False
            raise ProviderError("Open the breaker", status_code=503)

        attempt = attempts.get(request_id, 0) + 1
        attempts[request_id] = attempt
        raise ProviderError("Still failing", status_code=503)

    pipeline = make_pipeline(
        dispatch,
        max_retries=1,
        retry_base_ms=1,
        retry_max_delay_ms=1,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown_seconds=0.01,
    )

    await pipeline.call(CallInput(
        config=make_config(),
        messages=[{"role": "user", "content": "open-breaker"}],
        singleflight_key="open-breaker",
    ))
    await asyncio.sleep(0.02)

    for i in range(5):
        result = await pipeline.call(CallInput(
            config=make_config(),
            messages=[{"role": "user", "content": f"still-failing-{i}"}],
            singleflight_key=f"still-failing-{i}",
        ))
        assert_eq(result.success, False, f"half-open failed retry accounting: call {i + 1} fails")

    assert_eq(
        pipeline.get_circuit_breaker_state("openai", ""),
        "HALF_OPEN",
        "half-open failed retry accounting: breaker still waits for 10 admitted executions",
    )


async def test_circuit_breaker_scoped_by_effective_base_url() -> None:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        base_url = ctx.kwargs.get("base_url")
        if base_url == "https://bad.example/v1":
            raise ProviderError("upstream failed", status_code=500)
        return ProviderResult(content=str(base_url), model="gpt-4", usage=make_usage())

    def transform(ctx: dict[str, object], kwargs: dict[str, object]) -> dict[str, object]:
        return {**kwargs, "base_url": f"{ctx.get('base_url')}/v1"}

    pipeline = make_pipeline(
        dispatch,
        max_retries=0,
        circuit_breaker_threshold=1,
        transform_kwargs_overrides={"openai": transform},
    )

    bad = await pipeline.call(CallInput(
        config=make_config(baseUrl="https://bad.example"),
        messages=[{"role": "user", "content": "bad"}],
        singleflight_key="bad-endpoint",
    ))
    good = await pipeline.call(CallInput(
        config=make_config(baseUrl="https://good.example"),
        messages=[{"role": "user", "content": "good"}],
        singleflight_key="good-endpoint",
    ))

    assert_eq(bad.success, False, "circuit breaker base URL: failing endpoint fails")
    assert_eq(good.success, True, "circuit breaker base URL: healthy endpoint remains callable")
    assert_eq(good.content, "https://good.example/v1", "circuit breaker base URL: dispatch uses transformed URL")
    assert_eq(
        pipeline.get_circuit_breaker_state("openai", "https://bad.example/v1"),
        "OPEN",
        "circuit breaker base URL: failure opens only the bad endpoint breaker",
    )
    assert_eq(
        pipeline.get_circuit_breaker_state("openai", "https://good.example/v1"),
        "CLOSED",
        "circuit breaker base URL: healthy endpoint uses a separate breaker",
    )


async def test_singleflight_fallback_key_includes_base_url() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return ProviderResult(content=str(ctx.kwargs.get("base_url")), model="gpt-4", usage=make_usage())

    def transform(ctx: dict[str, object], kwargs: dict[str, object]) -> dict[str, object]:
        return {**kwargs, "base_url": ctx.get("base_url")}

    pipeline = make_pipeline(
        dispatch,
        transform_kwargs_overrides={"openai": transform},
    )
    messages = [{"role": "user", "content": "same"}]

    r1, r2 = await asyncio.gather(
        pipeline.call(CallInput(
            config=make_config(baseUrl="https://a.example"),
            messages=messages,
        )),
        pipeline.call(CallInput(
            config=make_config(baseUrl="https://b.example"),
            messages=messages,
        )),
    )

    assert_eq(r1.success, True, "singleflight base URL: first call succeeds")
    assert_eq(r2.success, True, "singleflight base URL: second call succeeds")
    assert_eq(r1.content, "https://a.example", "singleflight base URL: first response kept separate")
    assert_eq(r2.content, "https://b.example", "singleflight base URL: second response kept separate")
    assert_eq(call_count, 2, "singleflight base URL: different endpoints do not deduplicate together")


async def test_singleflight_fallback_key_sorts_provider_options() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return ProviderResult(content="ok", model="gpt-4", usage=make_usage())

    pipeline = make_pipeline(dispatch)
    messages = [{"role": "user", "content": "same"}]

    config_a = make_config(provider_options={"alpha": 1, "nested": {"x": 1, "y": 2}})
    config_b = make_config(provider_options={"nested": {"y": 2, "x": 1}, "alpha": 1})

    r1, r2 = await asyncio.gather(
        pipeline.call(CallInput(config=config_a, messages=messages)),
        pipeline.call(CallInput(config=config_b, messages=messages)),
    )

    assert_eq(r1.success, True, "singleflight sort keys: first call succeeds")
    assert_eq(r2.success, True, "singleflight sort keys: second call succeeds")
    assert_eq(call_count, 1, "singleflight sort keys: equivalent configs deduplicate together")


async def test_google_enable_thinking_keeps_provider_default_budget() -> None:
    captured_kwargs: dict[str, object] = {}

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        captured_kwargs.update(ctx.kwargs)
        return ProviderResult(content="ok", model="gemini-2.5-pro", usage=make_usage())

    pipeline = make_pipeline(dispatch)
    pipeline.set_key_pool("google", KeyPool(["google-test-key"]))

    result = await pipeline.call(CallInput(
        config={
            "provider": "google",
            "model": "gemini-2.5-pro",
            "common": {"enable_thinking": True},
        },
        messages=[{"role": "user", "content": "think"}],
        singleflight_key="google-thinking",
    ))

    assert_eq(result.success, True, "google enable_thinking: call succeeds")
    assert_true(
        "thinking_config" not in captured_kwargs,
        "google enable_thinking: pipeline does not inject thinking_budget=0",
    )


async def test_pipeline_forwards_common_sampling_controls() -> None:
    captured_kwargs: dict[str, object] = {}

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        captured_kwargs.update(ctx.kwargs)
        return ProviderResult(content="ok", model="Qwen/Qwen3-32B", usage=make_usage())

    pipeline = make_pipeline(dispatch)
    pipeline.set_key_pool("deepinfra", KeyPool(["deepinfra-test-key"]))

    result = await pipeline.call(CallInput(
        config={
            "provider": "deepinfra",
            "model": "Qwen/Qwen3-32B",
            "common": {
                "temperature": 0.15,
                "top_p": 0.92,
                "max_output_tokens": 384,
                "top_k": 64,
                "presence_penalty": 0.25,
                "frequency_penalty": 0.35,
                "stop_sequences": ["END"],
                "seed": 19,
            },
        },
        messages=[{"role": "user", "content": "sample"}],
        singleflight_key="sampling-controls",
    ))

    assert_eq(result.success, True, "pipeline sampling controls: call succeeds")
    assert_eq(captured_kwargs.get("temperature"), 0.15, "pipeline sampling controls: temperature forwarded")
    assert_eq(captured_kwargs.get("top_p"), 0.92, "pipeline sampling controls: top_p forwarded")
    assert_eq(captured_kwargs.get("max_tokens"), 384, "pipeline sampling controls: max_tokens forwarded")
    assert_eq(captured_kwargs.get("top_k"), 64, "pipeline sampling controls: top_k forwarded")
    assert_eq(captured_kwargs.get("presence_penalty"), 0.25, "pipeline sampling controls: presence_penalty forwarded")
    assert_eq(captured_kwargs.get("frequency_penalty"), 0.35, "pipeline sampling controls: frequency_penalty forwarded")
    assert_eq(captured_kwargs.get("stop"), ["END"], "pipeline sampling controls: stop_sequences normalized to stop")
    assert_eq(captured_kwargs.get("seed"), 19, "pipeline sampling controls: seed forwarded")


async def test_singleflight_fallback_key_includes_sampling_controls() -> None:
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        top_k = ctx.kwargs.get("top_k")
        return ProviderResult(content=str(top_k), model="Qwen/Qwen3-32B", usage=make_usage())

    pipeline = make_pipeline(dispatch)
    pipeline.set_key_pool("deepinfra", KeyPool(["deepinfra-test-key"]))
    messages = [{"role": "user", "content": "same"}]

    config_a = {
        "provider": "deepinfra",
        "model": "Qwen/Qwen3-32B",
        "common": {"top_k": 16},
    }
    config_b = {
        "provider": "deepinfra",
        "model": "Qwen/Qwen3-32B",
        "common": {"top_k": 32},
    }

    r1, r2 = await asyncio.gather(
        pipeline.call(CallInput(config=config_a, messages=messages)),
        pipeline.call(CallInput(config=config_b, messages=messages)),
    )

    assert_eq(r1.success, True, "singleflight sampling controls: first call succeeds")
    assert_eq(r2.success, True, "singleflight sampling controls: second call succeeds")
    assert_eq(r1.content, "16", "singleflight sampling controls: first result kept separate")
    assert_eq(r2.content, "32", "singleflight sampling controls: second result kept separate")
    assert_eq(call_count, 2, "singleflight sampling controls: different top_k values do not deduplicate together")


async def test_cache_skipped_when_response_has_tool_calls() -> None:
    """When provider returns tool_calls, the pipeline must not write to cache
    because CachedValue stores only text content, which would silently drop
    the function-call structure on a subsequent hit. (GH #6)
    """
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        return ProviderResult(
            content="calling tool",
            model="gpt-4",
            usage=make_usage(),
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}],
        )

    cache = TwoTierCache(strategy="memory")
    pipeline = make_pipeline(dispatch, response_cache=cache)
    config = make_config(caching={"strategy": "memory"})
    messages = [{"role": "user", "content": "what time?"}]

    r1 = await pipeline.call(CallInput(config=config, messages=messages))
    assert_eq(r1.success, True, "tool_calls skip-cache: first call succeeds")
    assert_true(r1.tool_calls is not None, "tool_calls skip-cache: first call returns tool_calls")

    r2 = await pipeline.call(CallInput(config=config, messages=messages))
    assert_eq(r2.success, True, "tool_calls skip-cache: second call succeeds")
    assert_eq(call_count, 2, "tool_calls skip-cache: dispatch called twice (cache was skipped)")
    assert_true(r2.tool_calls is not None, "tool_calls skip-cache: second call still returns tool_calls")


async def test_cache_used_when_response_has_no_tool_calls() -> None:
    """Baseline: normal text responses still get cached."""
    call_count = 0

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        nonlocal call_count
        call_count += 1
        return ProviderResult(content="cached reply", model="gpt-4", usage=make_usage())

    cache = TwoTierCache(strategy="memory")
    pipeline = make_pipeline(dispatch, response_cache=cache)
    config = make_config(caching={"strategy": "memory"})
    messages = [{"role": "user", "content": "hi"}]

    r1 = await pipeline.call(CallInput(config=config, messages=messages))
    r2 = await pipeline.call(CallInput(config=config, messages=messages))
    assert_eq(r1.success, True, "baseline cache: first succeeds")
    assert_eq(r2.success, True, "baseline cache: second succeeds")
    assert_eq(call_count, 1, "baseline cache: dispatch called once (cache hit on second)")
    assert_eq(r2.cache_hit, "l1", "baseline cache: second is a cache hit")


# =========================================================================
# Runner
# =========================================================================


async def main() -> None:
    await test_happy_path()
    await test_error_returns_failure()
    await test_thinking_stripping()
    await test_keep_thinking_output()
    await test_singleflight_dedup()
    await test_semaphore_release_on_failure()
    await test_semaphore_release_on_key_selection_failure()
    await test_circuit_breaker_trips()
    await test_circuit_breaker_only_counts_retryable()
    await test_circuit_breaker_ignores_local_validation_errors()
    await test_key_pool_rotation()
    await test_kill_switch()
    await test_retry_on_transient_error()
    await test_half_open_counts_per_admitted_execution()
    await test_half_open_counts_failed_retry_sequence_once()
    await test_circuit_breaker_scoped_by_effective_base_url()
    await test_singleflight_fallback_key_includes_base_url()
    await test_singleflight_fallback_key_sorts_provider_options()
    await test_google_enable_thinking_keeps_provider_default_budget()
    await test_pipeline_forwards_common_sampling_controls()
    await test_singleflight_fallback_key_includes_sampling_controls()
    await test_cache_skipped_when_response_has_tool_calls()
    await test_cache_used_when_response_has_no_tool_calls()

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
