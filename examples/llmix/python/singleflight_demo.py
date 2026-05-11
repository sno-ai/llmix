"""LLMix singleflight deduplication demo.

Demonstrates how concurrent identical requests are coalesced into
a single provider call. This saves API costs when multiple users
or goroutines request the same completion simultaneously.

Run with:
    OPENAI_API_KEY=sk-... uv run python examples/llmix/python/singleflight_demo.py
"""

import asyncio
import os
import time

from llmix import (
    CallInput,
    CallPipeline,
    KeyPool,
    PipelineConfig,
    TwoTierCache,
    openai_dispatch,
)


async def main() -> None:
    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
            response_cache=TwoTierCache("memory"),
            singleflight=True,  # Enable request coalescing
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    config = {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "common": {"temperature": 0.0, "max_output_tokens": 64},
        "caching": {"strategy": "memory"},
    }
    messages = [{"role": "user", "content": "What is 2 + 2?"}]

    # Fire 10 identical requests concurrently
    start = time.perf_counter()
    tasks = [
        pipeline.call(CallInput(config=config, messages=messages))
        for _ in range(10)
    ]
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    # All 10 get the same response, but only 1 API call was made
    print(f"Completed {len(results)} requests in {elapsed:.2f}s")
    print(f"Response: {results[0].content}")
    print(f"\nSingleflight stats:")
    print(f"  Total requests: {len(results)}")
    print(f"  Cache hits: {sum(1 for r in results if r.cache_hit)}")
    print(f"  Coalesced: {sum(1 for r in results if r.coalesced)}")
    print(f"  Actual API calls: {pipeline.stats.api_calls}")


if __name__ == "__main__":
    asyncio.run(main())
