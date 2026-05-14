"""LLMix runtime preset example.

The official secure registry release path is:
    mda release prepare -> llmix publish-registry -> mda release finalize
    -> mda doctor release -> llmix check-registry

This Python example is only the application runtime side. It assumes the app
repo already contains the generated registry files under config/llm from that
release flow:
    config/llm/source/search/summary.mda
    config/llm/current.json
    config/llm/compiled/

Keep the trust anchor outside config/llm.

Run with:
    OPENAI_API_KEY=sk-... uv run --project packages/llmix/python python examples/llmix/python/config_registry.py
"""

import asyncio
import os
from pathlib import Path

from llmix import (
    CallInput,
    CallPipeline,
    ConfigRegistryManager,
    KeyPool,
    PipelineConfig,
    TwoTierCache,
    openai_dispatch,
)


async def main() -> None:
    registry_root = Path("./config/llm")

    # Open the registry produced by the official MDA CLI + llmix command flow.
    registry = ConfigRegistryManager.open(registry_root)

    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
            response_cache=TwoTierCache("memory"),
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    # Load a named preset. Provider, model, and parameters come from the
    # published registry, not from application code.
    config = registry.get_preset("search", "summary")

    response = await pipeline.call(
        CallInput(
            config=config,
            messages=[
                {
                    "role": "user",
                    "content": "Summarize the key features of LLMix in three bullet points.",
                }
            ],
        )
    )
    print("Preset: search/summary")
    print(f"Provider: {config.get('provider')}, Model: {config.get('model')}")
    print(f"Response: {response.content}")
    print(f"Cache hit: {response.cache_hit}")

    # To update: edit config/llm/source/<module>/<preset>.mda, run the official
    # release flow again, and deploy the generated current.json and compiled/.


if __name__ == "__main__":
    asyncio.run(main())
