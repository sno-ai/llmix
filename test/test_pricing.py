#!/usr/bin/env python3
"""
Test suite for LLM pricing module.

Run with: python test_pricing.py
"""

import sys
import warnings
from pathlib import Path

# Add src/pricing to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "pricing"))

from pricing import get_model_pricing

# Suppress warnings during test (we test for null returns explicitly)
warnings.filterwarnings("ignore", module="pricing")

TEST_CASES: list[tuple[str, bool]] = [
    # Base models (should work)
    ("gpt-5-mini", True),
    ("gpt-5", True),
    ("claude-4.5-haiku", True),
    ("mistral-large", True),
    # With OpenAI date suffix -YYYY-MM-DD
    ("gpt-5-mini", True),
    ("gpt-5", True),
    ("gpt-5-pro", True),
    ("gpt-5.1", True),
    # With Anthropic date suffix -YYYYMMDD
    ("claude-haiku-4-5-20251001", True),
    ("claude-sonnet-4-5-20250929", True),
    # With Mistral date suffix -YYMM
    ("mistral-large-2411", True),
    # With prefix
    ("models/gemini-2.5-flash", True),
    ("Qwen/Qwen3-Reranker-4B", True),
    # Non-existent (should fail gracefully)
    ("nonexistent-model-xyz", False),
]


def main() -> int:
    """Run all tests and return exit code."""
    print("Testing get_model_pricing normalization:\n")

    passed = 0
    failed = 0

    for model, should_find in TEST_CASES:
        result = get_model_pricing(model)
        found = result is not None
        ok = found == should_find

        if ok:
            passed += 1
            print(f"[PASS] {model}")
        else:
            failed += 1
            expected = "found" if should_find else "null"
            actual = "found" if found else "null"
            print(f"[FAIL] {model} -> expected {expected}, got {actual}")

    print(f"\nResult: {passed} passed, {failed} failed")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
