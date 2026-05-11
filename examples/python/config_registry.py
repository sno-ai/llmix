"""LLMix Config Registry example.

Shows how to use ConfigRegistryManager to load MDA presets and
hot-swap models at runtime without redeploying.

Requires a presets directory with at least one .mda config file.
See docs/mda-vendor-namespace.md for the preset format.

Run with:
    OPENAI_API_KEY=sk-... uv run python examples/python/config_registry.py
"""

import asyncio
import os

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
    # Initialize the registry from a presets directory
    registry = ConfigRegistryManager(config_dir="./presets")

    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
            response_cache=TwoTierCache("memory"),
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    # Load a named preset — provider, model, and parameters come from
    # the MDA config file, not from application code
    config = registry.load_preset("summarize")

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
    print(f"Preset: summarize")
    print(f"Provider: {config.get('provider')}, Model: {config.get('model')}")
    print(f"Response: {response.content}")
    print(f"Cache hit: {response.cache_hit}")

    # To hot-swap: update the preset file on disk, then reload
    # registry.reload()
    # new_config = registry.load_preset("summarize")
    # The next call uses the updated provider/model — no redeploy needed.


if __name__ == "__main__":
    asyncio.run(main())
