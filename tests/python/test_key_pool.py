#!/usr/bin/env python3
"""
Unit tests for KeyPool and load_keys_from_env.

Run with: uv run python tests/python/test_key_pool.py
"""

import os
import sys
from pathlib import Path

# Add python/ to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from llmix.key_pool import KeyPool, KeyPoolExhaustedError, load_keys_from_env


def test_round_robin_order() -> None:
    """3 keys cycle through 1,2,3,1,2,3."""
    pool = KeyPool(["a", "b", "c"])
    results = []
    for _ in range(6):
        results.append(pool.select())
        pool.rotate()
    assert results == ["a", "b", "c", "a", "b", "c"], f"Expected round-robin, got {results}"
    print("+ round_robin_order")


def test_429_rotation() -> None:
    """rotate() advances to the next key."""
    pool = KeyPool(["a", "b", "c"])
    assert pool.select() == "a"
    pool.rotate()  # simulate 429
    assert pool.select() == "b"
    pool.rotate()  # simulate another 429
    assert pool.select() == "c"
    print("+ 429_rotation")


def test_select_does_not_advance_without_rotate() -> None:
    """select() is stable until rotate() is called."""
    pool = KeyPool(["a", "b", "c"])
    assert pool.select() == "a"
    assert pool.select() == "a"
    pool.rotate()
    assert pool.select() == "b"
    print("+ select_does_not_advance_without_rotate")


def test_dead_key_skip() -> None:
    """Dead keys are skipped during select()."""
    pool = KeyPool(["a", "b", "c"])
    pool.mark_dead("b")
    # Start at "a", rotate past dead "b" to "c"
    assert pool.select() == "a"
    pool.rotate()
    assert pool.select() == "c"
    pool.rotate()
    assert pool.select() == "a"
    print("+ dead_key_skip")


def test_all_exhausted_error() -> None:
    """KeyPoolExhaustedError when all keys are dead."""
    pool = KeyPool(["a", "b"])
    pool.mark_dead("a")
    pool.mark_dead("b")
    assert pool.is_exhausted()
    try:
        pool.select()
        assert False, "Should have raised KeyPoolExhaustedError"
    except KeyPoolExhaustedError:
        pass
    print("+ all_exhausted_error")


def test_whitespace_trimming() -> None:
    """Keys with whitespace are trimmed."""
    pool = KeyPool(["  a  ", " b ", "c  "])
    assert pool.select() == "a"
    pool.rotate()
    assert pool.select() == "b"
    pool.rotate()
    assert pool.select() == "c"
    print("+ whitespace_trimming")


def test_empty_key_filtering() -> None:
    """Empty strings and whitespace-only strings are filtered out."""
    pool = KeyPool(["a", "", "  ", "b"])
    assert pool.total_count == 2
    assert pool.select() == "a"
    pool.rotate()
    assert pool.select() == "b"
    print("+ empty_key_filtering")


def test_all_empty_raises() -> None:
    """Pool with only empty keys raises ValueError."""
    try:
        KeyPool(["", "  ", ""])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("+ all_empty_raises")


def test_empty_list_raises() -> None:
    """Empty list raises ValueError."""
    try:
        KeyPool([])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("+ empty_list_raises")


def test_alive_count() -> None:
    """alive_count decreases as keys die."""
    pool = KeyPool(["a", "b", "c"])
    assert pool.alive_count == 3
    pool.mark_dead("a")
    assert pool.alive_count == 2
    pool.mark_dead("b")
    assert pool.alive_count == 1
    print("+ alive_count")


def test_load_keys_from_env_multi() -> None:
    """load_keys_from_env reads {PROVIDER}_KEYS."""
    os.environ["TESTPROV_KEYS"] = "k1, k2, k3"
    try:
        pool = load_keys_from_env("testprov")
        assert pool.total_count == 3
        assert pool.select() == "k1"
        pool.rotate()
        assert pool.select() == "k2"
    finally:
        del os.environ["TESTPROV_KEYS"]
    print("+ load_keys_from_env_multi")


def test_load_keys_from_env_single_fallback() -> None:
    """load_keys_from_env falls back to {PROVIDER}_API_KEY."""
    os.environ["TESTPROV2_API_KEY"] = "single-key"
    # Ensure _KEYS is not set
    os.environ.pop("TESTPROV2_KEYS", None)
    try:
        pool = load_keys_from_env("TESTPROV2")
        assert pool.total_count == 1
        assert pool.select() == "single-key"
    finally:
        del os.environ["TESTPROV2_API_KEY"]
    print("+ load_keys_from_env_single_fallback")


def test_load_keys_from_env_missing_raises() -> None:
    """load_keys_from_env raises ValueError when neither var is set."""
    os.environ.pop("NOPROV_KEYS", None)
    os.environ.pop("NOPROV_API_KEY", None)
    try:
        load_keys_from_env("noprov")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "NOPROV" in str(e)
    print("+ load_keys_from_env_missing_raises")


def test_duplicate_keys_deduplicated() -> None:
    # Bug: passing ["a", "a", "b"] without dedup creates a pool of 3 where
    # round-robin returns "a" twice before "b". mark_dead("a") would then leave
    # a phantom second "a" that can still be selected. The dict.fromkeys()
    # dedup prevents this.
    pool = KeyPool(["a", "a", "b"])
    assert pool.total_count == 2, f"Expected 2 unique keys, got {pool.total_count}"
    assert pool.select() == "a"
    pool.rotate()
    assert pool.select() == "b"
    print("+ duplicate_keys_deduplicated")


def test_single_key_pool_rotate_stays_on_same_key() -> None:
    # rotate() on a single-key pool must not raise and must stay on the same key.
    pool = KeyPool(["only"])
    assert pool.select() == "only"
    pool.rotate()
    assert pool.select() == "only", "Single-key pool: rotate() stays on same key"
    print("+ single_key_pool_rotate_stays_on_same_key")


def test_single_key_pool_dead_immediately_exhausted() -> None:
    # Marking the only key dead must make the pool exhausted immediately.
    pool = KeyPool(["only"])
    pool.mark_dead("only")
    assert pool.is_exhausted(), "Single-key pool: after mark_dead, pool is exhausted"
    try:
        pool.select()
        assert False, "Should have raised KeyPoolExhaustedError"
    except KeyPoolExhaustedError:
        pass
    print("+ single_key_pool_dead_immediately_exhausted")


def test_mark_dead_unknown_key_raises() -> None:
    # mark_dead() must raise ValueError for a key that was never in the pool.
    # Without this guard a typo silently no-ops and the key keeps being served.
    pool = KeyPool(["a", "b"])
    try:
        pool.mark_dead("nonexistent-key")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("+ mark_dead_unknown_key_raises")


def test_load_keys_from_env_filters_empty() -> None:
    """load_keys_from_env filters empty entries from comma-separated list."""
    os.environ["TESTPROV3_KEYS"] = "k1,,  ,k2"
    try:
        pool = load_keys_from_env("testprov3")
        assert pool.total_count == 2
    finally:
        del os.environ["TESTPROV3_KEYS"]
    print("+ load_keys_from_env_filters_empty")


def main() -> int:
    print("Testing KeyPool:\n")
    tests = [
        test_round_robin_order,
        test_429_rotation,
        test_select_does_not_advance_without_rotate,
        test_dead_key_skip,
        test_all_exhausted_error,
        test_whitespace_trimming,
        test_empty_key_filtering,
        test_all_empty_raises,
        test_empty_list_raises,
        test_alive_count,
        test_duplicate_keys_deduplicated,
        test_single_key_pool_rotate_stays_on_same_key,
        test_single_key_pool_dead_immediately_exhausted,
        test_mark_dead_unknown_key_raises,
        test_load_keys_from_env_multi,
        test_load_keys_from_env_single_fallback,
        test_load_keys_from_env_missing_raises,
        test_load_keys_from_env_filters_empty,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"x {test.__name__}: {e}")

    print(f"\nResults: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
