"""LLMix batch embedding pipeline example.

Demonstrates high-throughput embedding generation with automatic
chunking, rate limit management, and progress tracking.

Run with:
    OPENAI_API_KEY=sk-... uv run python examples/llmix/python/batch_embedding.py
"""

import asyncio
import os

from llmix import (
    BatchConfig,
    CallPipeline,
    EmbeddingInput,
    KeyPool,
    PipelineConfig,
    openai_dispatch,
)


async def main() -> None:
    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    # Sample documents to embed
    documents = [
        "LLMix provides a unified API for multiple LLM providers.",
        "The circuit breaker prevents cascading failures during outages.",
        "Key rotation happens automatically on rate limit responses.",
        "Singleflight deduplication collapses concurrent identical requests.",
        "The two-tier cache supports memory L1 and Redis L2 backends.",
        "AIMD adaptive semaphore converges to optimal concurrency.",
        "MDA presets allow config-driven model swaps without redeploy.",
        "Batch embedding supports automatic chunking and progress tracking.",
    ]

    # Configure batch behavior
    batch_config = BatchConfig(
        max_batch_size=2048,    # Max inputs per API call
        max_concurrent=5,       # Concurrent API calls
        progress_callback=lambda done, total: print(f"  Progress: {done}/{total}"),
    )

    print(f"Embedding {len(documents)} documents...")
    results = await pipeline.embed_batch(
        EmbeddingInput(
            config={
                "provider": "openai",
                "model": "text-embedding-3-small",
                "kwargs": {"dimensions": 512},
            },
            texts=documents,
        ),
        batch_config=batch_config,
    )

    print(f"\nCompleted: {len(results.embeddings)} embeddings")
    print(f"Dimensions: {len(results.embeddings[0])}")
    print(f"Total tokens: {results.usage.total_tokens}")
    print(f"API calls made: {results.metadata.api_calls}")

    # Compute similarity between first two documents
    from numpy import dot
    from numpy.linalg import norm

    a, b = results.embeddings[0], results.embeddings[1]
    similarity = dot(a, b) / (norm(a) * norm(b))
    print(f"\nCosine similarity (doc 0 vs doc 1): {similarity:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
