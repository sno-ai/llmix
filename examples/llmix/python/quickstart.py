"""LLMix Python quickstart.

Run with:
    OPENAI_API_KEY=sk-... uv run --project packages/llmix/python python examples/llmix/python/quickstart.py
"""

import asyncio
import os

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

    response = await pipeline.call(
        CallInput(
            config={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "common": {"temperature": 0.7, "max_output_tokens": 256},
                "caching": {"strategy": "memory"},
            },
            messages=[
                {"role": "user", "content": "In one sentence, what is LLMix?"}
            ],
        )
    )

    print(response.content)
    print(f"cache_hit={response.cache_hit} usage={response.usage}")


if __name__ == "__main__":
    asyncio.run(main())
