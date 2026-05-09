#!/usr/bin/env python3
"""Tests for the LLMix resilience module (Python).

Covers circuit breaker, kill switch, singleflight, and retry logic.
Uses shared fixtures from tests/fixtures/circuit-breaker-scenarios.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import warnings
from pathlib import Path

# Ensure the python package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from llmix.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    KillSwitch,
    KillSwitchActiveError,
    RetryPolicy,
    Singleflight,
    calculate_delay,
    is_retryable,
    parse_retry_after,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
SCENARIOS_PATH = FIXTURE_DIR / "circuit-breaker-scenarios.json"


def load_scenarios() -> dict:
    with open(SCENARIOS_PATH) as f:
        return json.load(f)


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


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------

def test_circuit_breaker_closed_to_open() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    assert_eq(cb.state, CircuitState.CLOSED, "CB starts CLOSED")
    cb.on_failure(500)
    cb.on_failure(502)
    assert_eq(cb.state, CircuitState.CLOSED, "CB still CLOSED after 2 failures")
    cb.on_failure(503)
    assert_eq(cb.state, CircuitState.OPEN, "CB OPEN after 3 failures")


def test_circuit_breaker_auth_errors_ignored() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    for _ in range(10):
        cb.on_failure(401)
        cb.on_failure(403)
    assert_eq(cb.state, CircuitState.CLOSED, "401/403 do NOT trip breaker")


def test_circuit_breaker_non_retryable_ignored() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    cb.on_failure(400)
    cb.on_failure(404)
    cb.on_failure(422)
    assert_eq(cb.state, CircuitState.CLOSED, "4xx (non-429) do NOT trip breaker")


def test_circuit_breaker_success_resets() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_success()
    cb.on_failure(500)
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.CLOSED, "Success resets consecutive failure counter")


def test_circuit_breaker_open_blocks_check() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    try:
        cb.check()
        assert_true(False, "OPEN should raise CircuitOpenError")
    except CircuitOpenError:
        assert_true(True, "OPEN raises CircuitOpenError on check()")


def test_circuit_breaker_half_open_to_closed() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com", cooldown_seconds=0.01, permitted_half_open_calls=1)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.OPEN, "CB is OPEN")
    time.sleep(0.02)
    assert_eq(cb.state, CircuitState.HALF_OPEN, "CB transitions to HALF_OPEN after cooldown")
    cb.check()  # Allow probe
    cb.on_success()
    assert_eq(cb.state, CircuitState.CLOSED, "HALF_OPEN -> CLOSED on success")


def test_circuit_breaker_half_open_to_open() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com", cooldown_seconds=0.01, permitted_half_open_calls=1)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    time.sleep(0.02)
    assert_eq(cb.state, CircuitState.HALF_OPEN, "CB is HALF_OPEN")
    cb.check()  # Allow probe
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.OPEN, "HALF_OPEN -> OPEN on failure")


def test_circuit_breaker_half_open_blocks_when_full() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com", cooldown_seconds=0.01, permitted_half_open_calls=2)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    time.sleep(0.02)
    cb.check()  # Probe 1 allowed
    cb.check()  # Probe 2 allowed
    try:
        cb.check()  # Probe 3 blocked (only 2 permitted)
        assert_true(False, "Excess probe in HALF_OPEN should raise")
    except CircuitOpenError:
        assert_true(True, "HALF_OPEN blocks when all probe slots full")


def test_circuit_breaker_multi_probe_recovery() -> None:
    cb = CircuitBreaker("sno-gpu", "http://gpu:8080", cooldown_seconds=0.01, permitted_half_open_calls=3)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    time.sleep(0.02)
    # Allow 3 probes
    cb.check()
    cb.check()
    cb.check()
    # 2 succeed, 1 fails — majority success → CLOSED
    cb.on_success()
    cb.on_success()
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.CLOSED, "Multi-probe: majority success -> CLOSED")


def test_circuit_breaker_cancel_probe_no_double_count() -> None:
    cb = CircuitBreaker("sno-gpu", "http://gpu:8080", cooldown_seconds=0.01, permitted_half_open_calls=3)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    time.sleep(0.02)
    cb.check()
    cb.check()
    cb.check()
    # Probe 1: on_failure then cancel_probe (simulates the pipeline flow)
    cb.on_failure(500)
    cb.cancel_probe()  # Should be no-op — probe already finalized
    # Probe 2 & 3: succeed
    cb.on_success()
    cb.on_success()
    # Without the fix, failure is double-counted → 2 failures vs 2 successes → OPEN
    # With the fix, 1 failure vs 2 successes → CLOSED
    assert_eq(cb.state, CircuitState.CLOSED, "cancel_probe after on_failure must not double-count")


def test_circuit_breaker_429_trips() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    cb.on_failure(429)
    cb.on_failure(429)
    cb.on_failure(429)
    assert_eq(cb.state, CircuitState.OPEN, "429 trips the breaker")


def test_circuit_breaker_network_error_trips() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    cb.on_failure(network_error=True)
    cb.on_failure(network_error=True)
    cb.on_failure(network_error=True)
    assert_eq(cb.state, CircuitState.OPEN, "Network errors trip the breaker")


def test_circuit_breaker_reset() -> None:
    cb = CircuitBreaker("openai", "https://api.openai.com")
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.OPEN, "CB is OPEN")
    cb.reset()
    assert_eq(cb.state, CircuitState.CLOSED, "reset() -> CLOSED")


# ---------------------------------------------------------------------------
# Kill Switch Tests
# ---------------------------------------------------------------------------

def test_kill_switch_not_active() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ks = KillSwitch(state_dir=Path(tmp))
        ks.check()  # Should not raise
        assert_true(True, "Kill switch check passes when file absent")
        assert_eq(ks.is_active(), False, "is_active() returns False when file absent")


def test_kill_switch_active() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ks_path = Path(tmp) / "killswitch"
        ks_path.touch()
        ks = KillSwitch(state_dir=Path(tmp))
        try:
            ks.check()
            assert_true(False, "Should have raised KillSwitchActiveError")
        except KillSwitchActiveError:
            assert_true(True, "Kill switch raises when file present")
        assert_eq(ks.is_active(), True, "is_active() returns True when file present")


def test_kill_switch_env_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("LLMIX_STATE_DIR")
        os.environ["LLMIX_STATE_DIR"] = tmp
        try:
            ks = KillSwitch()
            assert_true(str(ks.path).startswith(tmp), "Kill switch resolves from LLMIX_STATE_DIR")
        finally:
            if old is None:
                del os.environ["LLMIX_STATE_DIR"]
            else:
                os.environ["LLMIX_STATE_DIR"] = old


# ---------------------------------------------------------------------------
# Singleflight Tests
# ---------------------------------------------------------------------------

def test_singleflight_dedup() -> None:
    sf = Singleflight()
    call_count = 0

    async def run() -> None:
        nonlocal call_count

        async def expensive() -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return "result"

        key = Singleflight.make_key("test-request")
        results = await asyncio.gather(
            sf.do(key, expensive),
            sf.do(key, expensive),
            sf.do(key, expensive),
        )

        assert_eq(call_count, 1, "Singleflight: fn called exactly once")
        assert_eq(list(results), ["result"] * 3, "Singleflight: all waiters get same result")

    asyncio.run(run())


def test_singleflight_error_propagation() -> None:
    sf = Singleflight()

    async def run() -> None:
        async def failing() -> str:
            await asyncio.sleep(0.01)
            raise ValueError("boom")

        key = Singleflight.make_key("fail-request")
        results = await asyncio.gather(
            sf.do(key, failing),
            sf.do(key, failing),
            return_exceptions=True,
        )

        assert_true(
            all(isinstance(r, ValueError) for r in results),
            "Singleflight: error propagated to all waiters",
        )

    asyncio.run(run())


def test_singleflight_cleanup() -> None:
    sf = Singleflight()

    async def run() -> None:
        async def work() -> int:
            return 42

        key = "test-key"
        await sf.do(key, work)
        assert_eq(sf.in_flight_count, 0, "Singleflight: map cleaned up after completion")

    asyncio.run(run())


def test_singleflight_make_key() -> None:
    key1 = Singleflight.make_key("hello")
    key2 = Singleflight.make_key("hello")
    key3 = Singleflight.make_key("world")
    assert_eq(key1, key2, "Same input produces same key")
    assert_true(key1 != key3, "Different input produces different key")
    assert_eq(len(key1), 64, "SHA-256 hex key is 64 chars")


# ---------------------------------------------------------------------------
# Retry Tests
# ---------------------------------------------------------------------------

def test_calculate_delay_exponential() -> None:
    # Test with zero jitter for predictable results
    d0 = calculate_delay(0, jitter_ms=0)
    d1 = calculate_delay(1, jitter_ms=0)
    d2 = calculate_delay(2, jitter_ms=0)
    d3 = calculate_delay(3, jitter_ms=0)
    assert_eq(d0, 1000, "attempt 0: 2^0 * 1000 = 1000")
    assert_eq(d1, 2000, "attempt 1: 2^1 * 1000 = 2000")
    assert_eq(d2, 4000, "attempt 2: 2^2 * 1000 = 4000")
    assert_eq(d3, 8000, "attempt 3: 2^3 * 1000 = 8000")


def test_calculate_delay_capped() -> None:
    d = calculate_delay(10, jitter_ms=0)
    assert_eq(d, 30000, "attempt 10: capped at 30000")


def test_calculate_delay_with_jitter() -> None:
    d = calculate_delay(0)
    assert_true(1000 <= d <= 2000, f"attempt 0 with jitter: {d} in [1000, 2000]")


def test_is_retryable() -> None:
    assert_eq(is_retryable(429), True, "429 is retryable")
    assert_eq(is_retryable(500), True, "500 is retryable")
    assert_eq(is_retryable(502), True, "502 is retryable")
    assert_eq(is_retryable(503), True, "503 is retryable")
    assert_eq(is_retryable(504), True, "504 is retryable")
    assert_eq(is_retryable(400), False, "400 is NOT retryable")
    assert_eq(is_retryable(401), False, "401 is NOT retryable")
    assert_eq(is_retryable(403), False, "403 is NOT retryable")
    assert_eq(is_retryable(404), False, "404 is NOT retryable")
    assert_eq(is_retryable(422), False, "422 is NOT retryable")
    assert_eq(is_retryable(200), False, "200 is NOT retryable")


def test_parse_retry_after() -> None:
    assert_eq(parse_retry_after("5"), 5000, "5 seconds -> 5000 ms")
    assert_eq(parse_retry_after("0"), 0, "0 seconds -> 0 ms")
    assert_eq(parse_retry_after("120"), 60000, "120 seconds capped at 60000 ms")
    assert_eq(parse_retry_after(None), None, "None -> None")
    assert_eq(parse_retry_after("abc"), None, "Invalid -> None")
    assert_eq(parse_retry_after("-1"), None, "Negative -> None")


def test_retry_policy_get_delay() -> None:
    policy = RetryPolicy(max_retries=3)
    # With Retry-After header
    d = policy.get_delay_ms(0, retry_after_header="10")
    assert_eq(d, 10000, "Retry-After takes precedence")

    # Without header, falls back to exponential
    d = policy.get_delay_ms(0, retry_after_header=None)
    assert_true(1000 <= d <= 2000, f"Fallback to exponential: {d} in [1000, 2000]")


def test_retry_policy_execute() -> None:
    attempt_count = 0

    async def run() -> None:
        nonlocal attempt_count
        policy = RetryPolicy(max_retries=2, base_ms=10, jitter_ms=0)

        async def failing_then_ok() -> str:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise RuntimeError("transient")
            return "ok"

        result = await policy.execute(failing_then_ok)
        assert_eq(result, "ok", "RetryPolicy: succeeds after retries")
        assert_eq(attempt_count, 3, "RetryPolicy: called 3 times (1 + 2 retries)")

    asyncio.run(run())


def test_retry_policy_non_retryable() -> None:
    async def run() -> None:
        policy = RetryPolicy(max_retries=3, base_ms=10, jitter_ms=0)
        call_count = 0

        async def always_fail() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent")

        try:
            await policy.execute(
                always_fail,
                is_retryable_fn=lambda _: False,
            )
            assert_true(False, "Should have raised")
        except ValueError:
            assert_eq(call_count, 1, "Non-retryable: only called once")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Fixture-driven tests
# ---------------------------------------------------------------------------

def test_circuit_breaker_cooldown_doubles_on_rehalf_open_failure() -> None:
    # Bug: after HALF_OPEN -> OPEN re-failure, cooldown doubles. If not
    # capped, the breaker stays OPEN far longer than intended on repeated
    # oscillations. The cap is 300 s; ensure it never exceeds that.
    cb = CircuitBreaker("openai", "https://api.openai.com", cooldown_seconds=10.0, permitted_half_open_calls=1)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.OPEN, "CB is OPEN")
    initial_cooldown = cb.cooldown_seconds

    # Simulate expired cooldown -> HALF_OPEN
    cb._opened_at = cb._opened_at - initial_cooldown - 1
    assert_eq(cb.state, CircuitState.HALF_OPEN, "CB enters HALF_OPEN after expired cooldown")

    # Probe admitted, then fails -> OPEN with doubled cooldown
    cb.check()
    cb.on_failure(500)
    assert_eq(cb.state, CircuitState.OPEN, "HALF_OPEN -> OPEN after probe failure")
    assert_eq(cb.cooldown_seconds, initial_cooldown * 2, "Cooldown doubled after re-open")

    # Drive cooldown to cap: set to 200 s, then fail -> 400 s capped at 300 s
    cb._base_cooldown = 10.0
    cb.cooldown_seconds = 200.0
    cb._opened_at = cb._opened_at - 201
    assert_eq(cb.state, CircuitState.HALF_OPEN, "CB enters HALF_OPEN again")
    cb.check()
    cb.on_failure(500)
    assert_eq(cb.cooldown_seconds, 300.0, "Cooldown capped at 300 s")


def test_circuit_breaker_half_open_tie_stays_open() -> None:
    # Bug: with 2 permitted probes, a 1-success + 1-failure result is a tie.
    # The condition `successes > failures` is strict greater-than, so a tie
    # means the circuit stays OPEN. Easy to miss if you assume ">=" semantics.
    cb = CircuitBreaker("openai", "https://api.openai.com", cooldown_seconds=10.0, permitted_half_open_calls=2)
    cb.on_failure(500)
    cb.on_failure(500)
    cb.on_failure(500)

    cb._opened_at = cb._opened_at - 11  # expire cooldown
    assert_eq(cb.state, CircuitState.HALF_OPEN, "CB in HALF_OPEN")

    cb.check()   # probe 1
    cb.check()   # probe 2
    cb.on_success()   # probe 1 succeeds
    cb.on_failure(500)   # probe 2 fails

    # successes == failures == 1 -> NOT a majority -> stays OPEN
    assert_eq(cb.state, CircuitState.OPEN, "Tie (1S + 1F) resolves to OPEN, not CLOSED")


def test_from_fixtures() -> None:
    scenarios = load_scenarios()

    # Verify retryable status codes
    for code in scenarios["retryableStatusCodes"]:
        assert_eq(is_retryable(code), True, f"Fixture: {code} is retryable")

    # Verify non-retryable status codes
    for code in scenarios["nonRetryableStatusCodes"]:
        assert_eq(is_retryable(code), False, f"Fixture: {code} is NOT retryable")

    # Verify retry delay ranges
    for entry in scenarios["retryDelayScenarios"]["attempts"]:
        d = calculate_delay(entry["attempt"])
        assert_true(
            entry["minMs"] <= d <= entry["maxMs"],
            f"Fixture: attempt {entry['attempt']} delay {d} in [{entry['minMs']}, {entry['maxMs']}]",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    test_circuit_breaker_closed_to_open()
    test_circuit_breaker_auth_errors_ignored()
    test_circuit_breaker_non_retryable_ignored()
    test_circuit_breaker_success_resets()
    test_circuit_breaker_open_blocks_check()
    test_circuit_breaker_half_open_to_closed()
    test_circuit_breaker_half_open_to_open()
    test_circuit_breaker_half_open_blocks_when_full()
    test_circuit_breaker_multi_probe_recovery()
    test_circuit_breaker_cancel_probe_no_double_count()
    test_circuit_breaker_429_trips()
    test_circuit_breaker_network_error_trips()
    test_circuit_breaker_reset()
    test_circuit_breaker_cooldown_doubles_on_rehalf_open_failure()
    test_circuit_breaker_half_open_tie_stays_open()
    test_kill_switch_not_active()
    test_kill_switch_active()
    test_kill_switch_env_resolution()
    test_singleflight_dedup()
    test_singleflight_error_propagation()
    test_singleflight_cleanup()
    test_singleflight_make_key()
    test_calculate_delay_exponential()
    test_calculate_delay_capped()
    test_calculate_delay_with_jitter()
    test_is_retryable()
    test_parse_retry_after()
    test_retry_policy_get_delay()
    test_retry_policy_execute()
    test_retry_policy_non_retryable()
    test_from_fixtures()

    print(f"\nResult: {passed} passed, {failed} failed")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
