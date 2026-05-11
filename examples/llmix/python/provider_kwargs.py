"""LLMix provider-specific kwargs passthrough example.

Shows how to pass provider-specific parameters (structured output,
tools, seed, stop sequences) through the unified config without
abstraction leakage.

Run with:
    OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-... \
    uv run python examples/llmix/python/provider_kwargs.py
"""

import asyncio
import os

from llmix import (
    CallInput,
    CallPipeline,
    KeyPool,
    PipelineConfig,
    anthropic_dispatch,
    openai_dispatch,
)


async def main() -> None:
    pipeline = CallPipeline(
        PipelineConfig(dispatch=openai_dispatch())
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))
    pipeline.set_key_pool("anthropic", KeyPool([os.environ["ANTHROPIC_API_KEY"]]))

    # OpenAI: structured output with JSON schema
    print("=== OpenAI: Structured Output ===")
    openai_resp = await pipeline.call(
        CallInput(
            config={
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "common": {"temperature": 0.0, "max_output_tokens": 256},
                "kwargs": {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "analysis",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "sentiment": {"type": "string"},
                                    "confidence": {"type": "number"},
                                },
                                "required": ["sentiment", "confidence"],
                            },
                        },
                    },
                    "seed": 42,  # Deterministic sampling
                },
            },
            messages=[
                {"role": "user", "content": "Analyze sentiment: 'LLMix is great!'"}
            ],
        )
    )
    print(f"  {openai_resp.content}")

    # Anthropic: system prompt and stop sequences
    print("\n=== Anthropic: System Prompt + Stop Sequences ===")
    anthropic_resp = await pipeline.call(
        CallInput(
            config={
                "provider": "anthropic",
                "model": "claude-sonnet-4-5-20250514",
                "common": {"temperature": 0.3, "max_output_tokens": 128},
                "kwargs": {
                    "system": "You are a concise technical writer.",
                    "stop_sequences": ["\n\n", "---"],
                },
            },
            messages=[
                {"role": "user", "content": "Define 'singleflight' in one sentence."}
            ],
        )
    )
    print(f"  {anthropic_resp.content}")
    print(f"  Stop reason: {anthropic_resp.metadata.stop_reason}")


if __name__ == "__main__":
    asyncio.run(main())
