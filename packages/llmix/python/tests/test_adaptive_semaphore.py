#!/usr/bin/env python3
"""Tests for AIMD Adaptive Semaphore.

Run with: uv run --project packages/llmix/python python packages/llmix/python/tests/test_adaptive_semaphore.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Add python/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.adaptive_semaphore import (
    AdaptiveSemaphore,
    parse_openai_ratelimit_headers,
)

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llmix"


def load_scenarios() -> dict:
    with open(FIXTURES_DIR / "aimd-scenarios.json", encoding="utf-8") as f:
        return json.load(f)


def apply_action(sem: AdaptiveSemaphore, action: dict) -> None:
    action_type = action["type"]
    if action_type == "success":
        sem.on_success()
    elif action_type == "rate_limit":
        sem.on_rate_limit()
    elif action_type == "header_feedback":
        sem.on_header_feedback(action["remaining"], action["limit"])
    else:
        raise ValueError(f"Unknown action type: {action_type}")


# ---- Fixture-driven scenario tests ----


def run_scenarios() -> tuple[int, int]:
    data = load_scenarios()
    passed = 0
    failed = 0

    for scenario in data["scenarios"]:
        name = scenario["name"]
        initial = scenario["initial"]
        sem = AdaptiveSemaphore(initial=initial)

        for action in scenario["actions"]:
            apply_action(sem, action)

        expected = scenario["expected_window"]
        if sem.window != expected:
            print(f"  FAIL: {name} — expected window={expected}, got {sem.window}")
            failed += 1
        else:
            print(f"  PASS: {name}")
            passed += 1

    return passed, failed


def run_header_parsing() -> tuple[int, int]:
    data = load_scenarios()
    passed = 0
    failed = 0

    for case in data["header_parsing"]:
        name = case["name"]
        result = parse_openai_ratelimit_headers(case["headers"])
        expected = case["expected"]

        if result != expected:
            print(f"  FAIL: header/{name} — expected {expected}, got {result}")
            failed += 1
        else:
            print(f"  PASS: header/{name}")
            passed += 1

    return passed, failed


# ---- Additional unit tests not in fixtures ----


def run_default_parameters() -> tuple[int, int]:
    """Default initial=32, min=4."""
    sem = AdaptiveSemaphore()
    assert sem.window == 32, f"Default window should be 32, got {sem.window}"
    assert sem.max_concurrency == 32
    assert sem.min_concurrency == 4
    print("  PASS: default_parameters")
    return 1, 0


def run_custom_min_concurrency() -> tuple[int, int]:
    """Custom min_concurrency is respected."""
    sem = AdaptiveSemaphore(initial=16, min_concurrency=8)
    for _ in range(10):
        sem.on_rate_limit()
    assert sem.window == 8, f"Expected floor=8, got {sem.window}"
    print("  PASS: custom_min_concurrency")
    return 1, 0


def run_header_zero_limit_ignored() -> tuple[int, int]:
    """on_header_feedback with limit=0 is a no-op."""
    sem = AdaptiveSemaphore(initial=16)
    sem.on_header_feedback(100, 0)
    assert sem.window == 16, f"Expected 16, got {sem.window}"
    print("  PASS: header_zero_limit_ignored")
    return 1, 0


def run_rebind() -> tuple[int, int]:
    """rebind() preserves window state."""
    sem = AdaptiveSemaphore(initial=32)
    sem.on_rate_limit()  # 32 -> 16
    assert sem.window == 16
    sem.rebind()
    assert sem.window == 16, f"Window should be preserved after rebind, got {sem.window}"
    print("  PASS: rebind")
    return 1, 0


def run_shrink_absorbs_future_releases() -> tuple[int, int]:
    """Shrinking below the in-flight permit count absorbs future releases."""

    async def scenario() -> None:
        sem = AdaptiveSemaphore(initial=4, min_concurrency=1)
        for _ in range(4):
            await sem.acquire()

        sem.on_rate_limit()  # 4 -> 2 while all permits are in-flight

        waiter = asyncio.create_task(sem.acquire())
        await asyncio.sleep(0)
        assert not waiter.done(), "waiter should block while all permits are held"

        sem.release()
        sem.release()
        await asyncio.sleep(0)
        assert not waiter.done(), "absorbed releases should not wake waiters"

        sem.release()
        await asyncio.sleep(0)
        assert waiter.done(), "waiter should wake once shrunken capacity is restored"

        waiter.result()
        sem.release()
        sem.release()

        first = asyncio.create_task(sem.acquire())
        second = asyncio.create_task(sem.acquire())
        third = asyncio.create_task(sem.acquire())
        await asyncio.sleep(0)

        assert first.done(), "first permit remains available after shrink"
        assert second.done(), "second permit remains available after shrink"
        assert not third.done(), "extra acquire blocks at the shrunken window"

        first.result()
        second.result()
        first.cancel()
        second.cancel()
        third.cancel()
        try:
            await third
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    print("  PASS: shrink_absorbs_future_releases")
    return 1, 0


def test_scenarios() -> None:
    passed, failed = run_scenarios()
    assert failed == 0, f"{failed} fixture-driven scenarios failed"
    assert passed > 0, "expected at least one fixture-driven scenario"


def test_header_parsing() -> None:
    passed, failed = run_header_parsing()
    assert failed == 0, f"{failed} header parsing cases failed"
    assert passed > 0, "expected at least one header parsing case"


def test_default_parameters() -> None:
    run_default_parameters()


def test_custom_min_concurrency() -> None:
    run_custom_min_concurrency()


def test_header_zero_limit_ignored() -> None:
    run_header_zero_limit_ignored()


def test_rebind() -> None:
    run_rebind()


def test_shrink_absorbs_future_releases() -> None:
    run_shrink_absorbs_future_releases()


def main() -> None:
    print("=== AIMD Adaptive Semaphore Tests ===\n")

    total_passed = 0
    total_failed = 0

    print("Fixture-driven scenarios:")
    p, f = run_scenarios()
    total_passed += p
    total_failed += f

    print("\nHeader parsing:")
    p, f = run_header_parsing()
    total_passed += p
    total_failed += f

    print("\nUnit tests:")
    for test_fn in [
        run_default_parameters,
        run_custom_min_concurrency,
        run_header_zero_limit_ignored,
        run_rebind,
        run_shrink_absorbs_future_releases,
    ]:
        p, f = test_fn()
        total_passed += p
        total_failed += f

    print(f"\n{'=' * 40}")
    print(f"Total: {total_passed} passed, {total_failed} failed")

    if total_failed > 0:
        sys.exit(1)
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
