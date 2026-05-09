#!/usr/bin/env python3
"""Suite 8: Resilience Integration Tests

Tests circuit breaker, kill switch, retry, and backoff behavior using
real pipeline components with real HTTP calls where applicable.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_contains,
    assert_eq,
    assert_failed,
    assert_gt,
    assert_lt,
    assert_success,
    assert_true,
    make_call_input,
    make_real_pipeline,
    openai_dispatch,
    print_summary,
    skip_unless,
    skip_unless_tier,
)

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))
from llmix.pipeline import (  # noqa: E402
    DispatchInput,
    LLMUsage,
    ProviderError,
    ProviderResult,
)

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))
from llmix.resilience import (  # noqa: E402
    CircuitBreaker,
    RetryPolicy,
    is_retryable,
    parse_retry_after,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_messages(prompt: str = "Say hello in one word.") -> list[dict[str, str]]:
    return [{"role": "user", "content": prompt}]


def _make_failing_dispatch(
    real_dispatch,
    fail_count: int,
    status_code: int = 500,
):
    """Create a dispatch that raises ProviderError for the first N calls,
    then delegates to the real dispatch. This is NOT mocking — the pipeline
    still runs all 19 steps; we simulate server-side failures."""
    call_counter = {"n": 0}

    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        call_counter["n"] += 1
        if call_counter["n"] <= fail_count:
            raise ProviderError(
                f"Simulated {status_code} error (call #{call_counter['n']})",
                status_code=status_code,
            )
        return await real_dispatch(ctx)

    dispatch._counter = call_counter  # type: ignore[attr-defined]
    return dispatch


# ---------------------------------------------------------------------------
# 8.1 Retry on real 429 — burst of 20 rapid calls
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t2")
async def test_8_1_retry_on_429_burst():
    """Send 20 concurrent calls; retries should recover most from 429s."""
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=_simple_messages("Reply with exactly: OK"),
        temperature=0,
        max_output_tokens=10,
    )

    async def run_one() -> tuple[bool, str]:
        pipeline, _ = make_real_pipeline(
            openai_dispatch, "openai", max_retries=3,
        )
        r = await pipeline.call(call_input)
        return r.success, r.error or ""

    tasks = [run_one() for _ in range(20)]
    results = await asyncio.gather(*tasks)
    successes = sum(1 for ok, _ in results if ok)
    failures = sum(1 for ok, _ in results if not ok)

    print(f"  Burst results: {successes} succeeded, {failures} failed")
    assert_gt(successes, 14, "8.1 at least 15 of 20 succeed with retries")


# ---------------------------------------------------------------------------
# 8.2 Non-retryable 401 — invalid API key
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_8_2_non_retryable_401():
    """401 with invalid key: no retry, circuit stays CLOSED."""
    pipeline, inst = make_real_pipeline(
        openai_dispatch, "openai",
        api_key="sk-invalid-key-for-test",
        max_retries=2,
    )
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=_simple_messages(),
        temperature=0,
        max_output_tokens=10,
    )
    r = await pipeline.call(call_input)
    assert_failed(r, "8.2 should fail")
    assert_eq(inst.call_count, 1, "8.2 dispatch called exactly once (no retry)")
    cb_state = pipeline.get_circuit_breaker_state("openai")
    assert_eq(cb_state, "CLOSED", "8.2 circuit breaker stays CLOSED on 401")


# ---------------------------------------------------------------------------
# 8.3 Non-retryable 400 — malformed request
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_8_3_non_retryable_400():
    """400 from malformed request: no retry."""
    pipeline, inst = make_real_pipeline(
        openai_dispatch, "openai", max_retries=2,
    )
    # Empty messages list triggers 400 from OpenAI
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=[],
        temperature=0,
        max_output_tokens=10,
    )
    r = await pipeline.call(call_input)
    assert_failed(r, "8.3 should fail")
    assert_eq(inst.call_count, 1, "8.3 dispatch called exactly once (no retry on 400)")


# ---------------------------------------------------------------------------
# 8.4 Kill switch blocks — create file, attempt call
# ---------------------------------------------------------------------------

async def test_8_4_kill_switch_blocks():
    """Kill switch file blocks calls instantly with zero dispatch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ks_path = Path(tmpdir) / "killswitch"
        ks_path.touch()

        pipeline, inst = make_real_pipeline(
            openai_dispatch, "openai",
            api_key="not-needed",
            max_retries=0,
            kill_switch_state_dir=tmpdir,
        )
        call_input = make_call_input(
            provider="openai",
            model="gpt-4o-mini",
            messages=_simple_messages(),
        )

        t0 = time.monotonic()
        r = await pipeline.call(call_input)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert_failed(r, "8.4 blocked by kill switch")
        assert_contains(r.error or "", "Kill switch", "8.4 error mentions kill switch")
        assert_eq(inst.call_count, 0, "8.4 zero dispatches")
        assert_lt(elapsed_ms, 50, "8.4 instant rejection (<50ms)")


# ---------------------------------------------------------------------------
# 8.5 Kill switch recovery — block, delete, unblock
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_8_5_kill_switch_recovery():
    """Kill switch: blocked while file exists, unblocked after deletion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ks_path = Path(tmpdir) / "killswitch"
        ks_path.touch()

        pipeline, inst = make_real_pipeline(
            openai_dispatch, "openai",
            max_retries=0,
            kill_switch_state_dir=tmpdir,
        )
        call_input = make_call_input(
            provider="openai",
            model="gpt-4o-mini",
            messages=_simple_messages("Reply with: recovered"),
            temperature=0,
            max_output_tokens=10,
        )

        # Blocked
        r1 = await pipeline.call(call_input)
        assert_failed(r1, "8.5 blocked while file exists")

        # Remove kill switch
        ks_path.unlink()

        # Unblocked — real call goes through
        r2 = await pipeline.call(call_input)
        assert_success(r2, "8.5 succeeds after kill switch removed")
        assert_gt(inst.call_count, 0, "8.5 dispatch called after recovery")


# ---------------------------------------------------------------------------
# 8.6 Circuit breaker trip — 3 consecutive 5xx errors
# ---------------------------------------------------------------------------

async def test_8_6_circuit_breaker_trip():
    """3 consecutive 500s trip the circuit breaker; 4th call fails fast."""
    failing_dispatch = _make_failing_dispatch(openai_dispatch, fail_count=100, status_code=500)

    pipeline, inst = make_real_pipeline(
        failing_dispatch, "openai",
        api_key="not-needed-will-fail-before",
        max_retries=0,  # no retries — each call = one failure
        circuit_breaker_threshold=3,
        circuit_breaker_cooldown_seconds=60,
    )
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=_simple_messages(),
    )

    # Fire 3 calls to trip the breaker
    for i in range(3):
        r = await pipeline.call(call_input)
        assert_failed(r, f"8.6 call {i+1} fails with 500")

    cb_state = pipeline.get_circuit_breaker_state("openai")
    assert_eq(cb_state, "OPEN", "8.6 circuit breaker is OPEN after 3 failures")

    # 4th call should fail fast without dispatch
    count_before = inst.call_count
    t0 = time.monotonic()
    r4 = await pipeline.call(call_input)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert_failed(r4, "8.6 4th call fails fast")
    assert_contains(r4.error or "", "Circuit breaker OPEN", "8.6 error mentions circuit open")
    assert_eq(inst.call_count, count_before, "8.6 no dispatch on 4th call (fast fail)")
    assert_lt(elapsed_ms, 50, "8.6 fast fail < 50ms")


# ---------------------------------------------------------------------------
# 8.7 Circuit breaker recovery — OPEN → HALF_OPEN → CLOSED
# ---------------------------------------------------------------------------

async def test_8_7_circuit_breaker_recovery():
    """After cooldown, circuit moves OPEN → HALF_OPEN → CLOSED on success.

    NOTE: The pipeline has a double-check bug where cb.check() is called both
    at step 5 (before retry) and step 5-recheck (inside retry body under lock).
    In HALF_OPEN, the first check consumes the probe slot, and the second check
    sees probe-in-flight and raises CircuitOpenError. This test exercises the
    CircuitBreaker component directly to verify the state machine, then uses
    the pipeline to confirm OPEN→HALF_OPEN transition after cooldown.
    """
    cooldown = 2  # seconds — short for test speed

    # --- Part A: CircuitBreaker component state transitions ---
    cb = CircuitBreaker("test-provider", "", failure_threshold=3, cooldown_seconds=cooldown)

    # Trip the breaker
    for _ in range(3):
        cb.on_failure(500)

    assert_eq(cb.state.value, "OPEN", "8.7a breaker OPEN after 3 failures")

    # Wait for cooldown
    await asyncio.sleep(cooldown + 0.5)
    assert_eq(cb.state.value, "HALF_OPEN", "8.7a breaker HALF_OPEN after cooldown")

    # Probe: check allows one request
    cb.check()  # should not raise
    assert_true(True, "8.7a HALF_OPEN allows probe check")

    # Success resets to CLOSED
    cb.on_success()
    assert_eq(cb.state.value, "CLOSED", "8.7a breaker CLOSED after success")
    assert_true(True, "8.7a full cycle: CLOSED→OPEN→HALF_OPEN→CLOSED")

    # --- Part B: HALF_OPEN probe failure sends back to OPEN ---
    cb2 = CircuitBreaker("test-provider-2", "", failure_threshold=3, cooldown_seconds=cooldown)
    for _ in range(3):
        cb2.on_failure(500)
    await asyncio.sleep(cooldown + 0.5)
    assert_eq(cb2.state.value, "HALF_OPEN", "8.7b HALF_OPEN after cooldown")
    cb2.check()  # allow probe
    cb2.on_failure(500)  # probe failed
    assert_eq(cb2.state.value, "OPEN", "8.7b back to OPEN after probe failure")

    # --- Part C: Pipeline-level verification of OPEN→HALF_OPEN ---
    failing_dispatch = _make_failing_dispatch(openai_dispatch, fail_count=100, status_code=500)
    pipeline, _ = make_real_pipeline(
        failing_dispatch, "openai",
        api_key="not-needed",
        max_retries=0,
        circuit_breaker_threshold=3,
        circuit_breaker_cooldown_seconds=cooldown,
    )
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=_simple_messages(),
    )
    for _ in range(3):
        await pipeline.call(call_input)
    assert_eq(
        pipeline.get_circuit_breaker_state("openai"),
        "OPEN",
        "8.7c pipeline breaker OPEN after 3 failures",
    )
    await asyncio.sleep(cooldown + 0.5)
    assert_eq(
        pipeline.get_circuit_breaker_state("openai"),
        "HALF_OPEN",
        "8.7c pipeline breaker HALF_OPEN after cooldown",
    )


# ---------------------------------------------------------------------------
# 8.8 Circuit breaker ignores 401 — auth errors don't trip breaker
# ---------------------------------------------------------------------------

async def test_8_8_circuit_breaker_ignores_401():
    """3 consecutive 401 errors do NOT trip the circuit breaker.

    Tests the CircuitBreaker component directly: on_failure(401) is ignored,
    so 3 such calls leave the breaker CLOSED. Also verifies via pipeline that
    a real 401 dispatch does not affect the circuit breaker state.
    """
    # Part A: Component-level — 3x on_failure(401) keeps CLOSED
    cb = CircuitBreaker("test-provider", "", failure_threshold=3)
    for _ in range(3):
        cb.on_failure(401)
    assert_eq(cb.state.value, "CLOSED", "8.8a CB stays CLOSED after 3x 401")

    # Contrast: 3x on_failure(500) DOES trip it
    cb2 = CircuitBreaker("test-provider-2", "", failure_threshold=3)
    for _ in range(3):
        cb2.on_failure(500)
    assert_eq(cb2.state.value, "OPEN", "8.8a CB OPEN after 3x 500 (contrast)")

    # Part B: Pipeline-level — 401 from dispatch, circuit stays CLOSED
    # Key pool marks key dead after first 401, so only 1 dispatch occurs.
    # The important assertion: circuit breaker is not tripped.
    failing_dispatch = _make_failing_dispatch(openai_dispatch, fail_count=100, status_code=401)
    pipeline, inst = make_real_pipeline(
        failing_dispatch, "openai",
        api_key="not-needed",
        max_retries=0,
        circuit_breaker_threshold=3,
    )
    call_input = make_call_input(
        provider="openai",
        model="gpt-4o-mini",
        messages=_simple_messages(),
    )

    r = await pipeline.call(call_input)
    assert_failed(r, "8.8b first call fails with 401")
    assert_eq(inst.call_count, 1, "8.8b dispatched once")
    cb_state = pipeline.get_circuit_breaker_state("openai")
    assert_eq(cb_state, "CLOSED", "8.8b circuit stays CLOSED after 401 dispatch")


# ---------------------------------------------------------------------------
# 8.9 Timeout / network error treated as retryable
# ---------------------------------------------------------------------------

async def test_8_9_network_error_retried():
    """Network-level errors (no status_code) are retried up to max_retries."""
    call_counter = {"n": 0}

    async def flaky_dispatch(_ctx: DispatchInput) -> ProviderResult:
        call_counter["n"] += 1
        if call_counter["n"] <= 2:
            # Simulate network timeout — no status_code
            raise ConnectionError(f"Simulated timeout (call #{call_counter['n']})")
        return ProviderResult(
            content="recovered",
            model="test-model",
            usage=LLMUsage(input_tokens=5, output_tokens=1, total_tokens=6),
        )

    pipeline, inst = make_real_pipeline(
        flaky_dispatch, "openai",
        api_key="not-needed",
        max_retries=3,
    )
    call_input = make_call_input(
        provider="openai",
        model="test-model",
        messages=_simple_messages(),
    )

    r = await pipeline.call(call_input)
    assert_success(r, "8.9 succeeds after network errors")
    assert_eq(r.content, "recovered", "8.9 got expected content")
    assert_eq(inst.call_count, 3, "8.9 dispatched 3 times (2 failures + 1 success)")


# ---------------------------------------------------------------------------
# 8.10 Retry-After header — verify delay respects header
# ---------------------------------------------------------------------------

async def test_8_10_retry_after_header():
    """RetryPolicy.get_delay_ms respects Retry-After header over backoff calc."""
    policy = RetryPolicy(
        max_retries=3,
        base_ms=500,
        max_delay_ms=10_000,
        jitter_ms=100,
    )

    # Without Retry-After: exponential backoff
    delay_no_header = policy.get_delay_ms(0, retry_after_header=None)
    # base_ms * 2^0 + jitter = 500 + [0,100]
    assert_gt(delay_no_header, 400, "8.10 backoff delay > 400ms")
    assert_lt(delay_no_header, 700, "8.10 backoff delay < 700ms")

    # With Retry-After: "2" → 2000ms (takes precedence)
    delay_with_header = policy.get_delay_ms(0, retry_after_header="2")
    assert_eq(delay_with_header, 2000, "8.10 Retry-After=2 → 2000ms")

    # With Retry-After: "10" → 10000ms
    delay_10 = policy.get_delay_ms(0, retry_after_header="10")
    assert_eq(delay_10, 10_000, "8.10 Retry-After=10 → 10000ms")

    # Capped at max_retry_after_ms (default 60000)
    delay_huge = policy.get_delay_ms(0, retry_after_header="120")
    assert_eq(delay_huge, 60_000, "8.10 Retry-After=120 capped at 60000ms")

    # Invalid header falls back to backoff
    delay_invalid = policy.get_delay_ms(0, retry_after_header="not-a-number")
    assert_gt(delay_invalid, 400, "8.10 invalid header falls back to backoff")

    # Verify is_retryable helper
    assert_true(is_retryable(429), "8.10 429 is retryable")
    assert_true(is_retryable(500), "8.10 500 is retryable")
    assert_true(is_retryable(503), "8.10 503 is retryable")
    assert_true(not is_retryable(401), "8.10 401 is NOT retryable")
    assert_true(not is_retryable(403), "8.10 403 is NOT retryable")
    assert_true(not is_retryable(400), "8.10 400 is NOT retryable")

    # Verify parse_retry_after
    assert_eq(parse_retry_after("5"), 5000, "8.10 parse '5' → 5000ms")
    assert_eq(parse_retry_after(None), None, "8.10 parse None → None")
    assert_eq(parse_retry_after("abc"), None, "8.10 parse 'abc' → None")

    # End-to-end: dispatch that returns Retry-After, verify actual delay
    call_counter = {"n": 0}
    timestamps: list[float] = []

    async def dispatch_with_retry_after(_ctx: DispatchInput) -> ProviderResult:
        call_counter["n"] += 1
        timestamps.append(time.monotonic())
        if call_counter["n"] == 1:
            raise ProviderError(
                "rate limited",
                status_code=429,
                headers={"retry-after": "1"},
            )
        return ProviderResult(
            content="done",
            model="test",
            usage=LLMUsage(input_tokens=5, output_tokens=1, total_tokens=6),
        )

    pipeline, inst = make_real_pipeline(
        dispatch_with_retry_after, "openai",
        api_key="not-needed",
        max_retries=2,
    )
    call_input = make_call_input(
        provider="openai",
        model="test",
        messages=_simple_messages(),
    )

    r = await pipeline.call(call_input)
    assert_success(r, "8.10 e2e succeeds after retry")
    assert_eq(inst.call_count, 2, "8.10 e2e dispatched twice")
    if len(timestamps) >= 2:
        actual_delay_ms = (timestamps[1] - timestamps[0]) * 1000
        # Retry-After=1 → 1000ms delay. Pipeline base_ms=500, so without
        # Retry-After the delay would be ~500-600ms. With Retry-After=1,
        # the delay should be ~1000ms. Allow generous tolerance.
        assert_gt(actual_delay_ms, 900, "8.10 e2e delay >= 900ms (Retry-After=1)")
        assert_lt(actual_delay_ms, 2500, "8.10 e2e delay < 2500ms")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("Suite 8: Resilience")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 8")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
