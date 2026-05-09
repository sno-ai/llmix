"""
LLMix Python import time benchmark.

Measures how long `import llmix` takes. Target: < 100ms when no
provider SDKs are installed (core-only path).

Run:
    uv run python tests/python/bench_import_time.py
"""

from __future__ import annotations

import sys
import time


def bench_import(iterations: int = 5) -> None:
    """Benchmark ``import llmix`` by forcing a fresh import each run."""
    times: list[float] = []
    for i in range(iterations):
        # Remove cached llmix modules so re-import is measured
        to_remove = [key for key in sys.modules if key == "llmix" or key.startswith("llmix.")]
        for key in to_remove:
            del sys.modules[key]

        start = time.perf_counter()
        import llmix  # noqa: F401
        elapsed_ms = (time.perf_counter() - start) * 1000
        times.append(elapsed_ms)
        print(f"  Run {i + 1}: {elapsed_ms:.1f}ms")

    avg = sum(times) / len(times)
    best = min(times)
    worst = max(times)
    print(f"\nResults ({iterations} iterations):")
    print(f"  Average: {avg:.1f}ms")
    print(f"  Best:    {best:.1f}ms")
    print(f"  Worst:   {worst:.1f}ms")
    print(f"  Target:  < 100ms")
    if avg < 100:
        print("  Status:  PASS")
    else:
        print("  Status:  ABOVE TARGET (may include provider SDK imports)")


if __name__ == "__main__":
    print("LLMix Python import time benchmark\n")
    bench_import()
