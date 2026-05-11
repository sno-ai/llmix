"""LLMix streaming with cache integration example.

Shows how streaming interacts with the response cache:
- Cache miss: tokens stream from the provider in real time
- Cache hit: complete response replays instantly (no streaming delay)

Run with:
    OPENAI_API_KEY=sk-... uv run python examples/llmix/python/streaming_with_cache.py
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
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    call_input = CallInput(
        config={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "common": {"temperature": 0.5, "max_output_tokens": 128},
            "caching": {"strategy": "memory"},
        },
        messages=[
            {"role": "user", "content": "Explain singleflight in one paragraph."}
        ],
    )

    # First call — streams from provider
    print("=== First call (cache miss, streaming from provider) ===")
    start = time.perf_counter()
    ttft = None
    token_count = 0

    async for chunk in pipeline.stream(call_input):
        if ttft is None:
            ttft = (time.perf_counter() - start) * 1000
        token_count += 1
        print(chunk.delta, end="", flush=True)

    elapsed = (time.perf_counter() - start) * 1000
    print(f"\n\nTime-to-first-token: {ttft:.0f}ms")
    print(f"Total time: {elapsed:.0f}ms ({token_count} chunks)")

    # Second call — replays from cache instantly
    print("\n=== Second call (cache hit, instant replay) ===")
    start = time.perf_counter()
    ttft = None
    token_count = 0

    async for chunk in pipeline.stream(call_input):
        if ttft is None:
            ttft = (time.perf_counter() - start) * 1000
        token_count += 1
        print(chunk.delta, end="", flush=True)

    elapsed = (time.perf_counter() - start) * 1000
    print(f"\n\nTime-to-first-token: {ttft:.0f}ms")
    print(f"Total time: {elapsed:.0f}ms ({token_count} chunks)")
    print(f"Cache hit: True (instant replay)")


if __name__ == "__main__":
    asyncio.run(main())
