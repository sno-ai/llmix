"""LLMix multi-provider dispatch example.

Shows how to configure multiple providers in a single pipeline and
dispatch to each independently. The cache and circuit breaker maintain
separate state per provider.

Run with:
    OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-... \
    uv run --project packages/llmix/python python examples/llmix/python/multi_provider.py
"""

import asyncio
import os

from llmix import (
    CallInput,
    CallPipeline,
    KeyPool,
    PipelineConfig,
    TwoTierCache,
    anthropic_dispatch,
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
    pipeline.set_key_pool(
        "anthropic", KeyPool([os.environ["ANTHROPIC_API_KEY"]])
    )

    prompt = "In one sentence, explain what a circuit breaker pattern is."

    # Dispatch to OpenAI
    openai_resp = await pipeline.call(
        CallInput(
            config={
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "common": {"temperature": 0.3, "max_output_tokens": 128},
                "caching": {"strategy": "memory"},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    )
    print(f"[OpenAI]    {openai_resp.content}")

    # Dispatch to Anthropic
    anthropic_resp = await pipeline.call(
        CallInput(
            config={
                "provider": "anthropic",
                "model": "claude-sonnet-4-5-20250514",
                "common": {"temperature": 0.3, "max_output_tokens": 128},
                "caching": {"strategy": "memory"},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    )
    print(f"[Anthropic] {anthropic_resp.content}")

    # Same calls again — should hit cache
    cached = await pipeline.call(
        CallInput(
            config={
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "common": {"temperature": 0.3, "max_output_tokens": 128},
                "caching": {"strategy": "memory"},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    )
    print(f"\n[OpenAI cached] cache_hit={cached.cache_hit}")


if __name__ == "__main__":
    asyncio.run(main())
