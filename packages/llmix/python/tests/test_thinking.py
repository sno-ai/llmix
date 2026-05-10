#!/usr/bin/env python3
"""
Thinking token stripping tests.
Consumes shared test vectors from fixtures/thinking-strip-vectors.json.

Run with: uv run --project packages/llmix/python python packages/llmix/python/tests/test_thinking.py
"""

import json
import sys
from pathlib import Path

# Add the package root to path so llmix is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.thinking import strip_thinking  # noqa: E402

fixture_dir = Path(__file__).resolve().parents[4] / "fixtures" / "llmix"
vectors_file = fixture_dir / "thinking-strip-vectors.json"
vectors_data = json.loads(vectors_file.read_text())

passed = 0
failed = 0


def assert_eq(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"+ {msg}")
    else:
        failed += 1
        print(f"x {msg}")


# Run shared test vectors
for vec in vectors_data["vectors"]:
    content, thinking = strip_thinking(vec["input"])
    assert_eq(
        content == vec["expectedContent"],
        f"[{vec['name']}] content: got {content!r}, expected {vec['expectedContent']!r}",
    )
    assert_eq(
        thinking == vec["expectedThinking"],
        f"[{vec['name']}] thinking: got {thinking!r}, expected {vec['expectedThinking']!r}",
    )

# keepThinkingOutput override test
keep_input = "<think>reasoning</think>answer"
content, thinking = strip_thinking(keep_input)
assert_eq(
    content == "answer" and thinking == "reasoning",
    "strip_thinking correctly strips (caller decides whether to apply based on keep_thinking_output)",
)

# When keep_thinking_output=True, caller should skip strip_thinking entirely
assert_eq(
    keep_input == "<think>reasoning</think>answer",
    "keep_thinking_output=True: raw content preserved when strip_thinking is not called",
)

print(f"\n{passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
