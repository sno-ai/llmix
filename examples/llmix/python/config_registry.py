"""LLMix Config Registry example.

Shows how to publish MDA presets into the Config Registry, then load them with
ConfigRegistryManager at runtime.

Requires a registry root with at least one authoring preset, for example:
    config/llm/authoring/search/summary.mda

See docs/secure-llmix-configuration.md for the preset format.

Run with:
    OPENAI_API_KEY=sk-... uv run python examples/python/config_registry.py
"""

import asyncio
import os
from pathlib import Path

from llmix import (
    CallInput,
    CallPipeline,
    ConfigRegistryManager,
    ConfigRegistryPublisher,
    KeyPool,
    PipelineConfig,
    TwoTierCache,
    openai_dispatch,
)


async def main() -> None:
    registry_root = Path("./config/llm")

    # Publish authoring/*.mda files into an immutable runtime snapshot.
    ConfigRegistryPublisher(registry_root).publish()
    registry = ConfigRegistryManager.open(registry_root)

    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
            response_cache=TwoTierCache("memory"),
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    # Load a named preset. Provider, model, and parameters come from the
    # published registry snapshot, not from application code.
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

    # To hot-swap: update the authoring MDA, publish a new revision, and the
    # manager will observe current.json on the next preset lookup.
    # ConfigRegistryPublisher(registry_root).publish()
    # new_config = registry.get_preset("search", "summary")


if __name__ == "__main__":
    asyncio.run(main())
