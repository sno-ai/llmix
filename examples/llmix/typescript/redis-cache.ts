/**
 * LLMix two-tier cache with Redis L2 example.
 *
 * Requires a running Redis instance. Run with:
 *   OPENAI_API_KEY=sk-... REDIS_URL=redis://localhost:6379 \
 *   bun run examples/typescript/redis-cache.ts
 */

import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
} from "@snoai/llmix";

async function main(): Promise<void> {
  const redisUrl = process.env["REDIS_URL"] ?? "redis://localhost:6379";

  const pipeline = new CallPipeline({
    dispatch: openaiDispatch(),
    responseCache: new TwoTierCache("redis", { redisUrl }),
  });
  pipeline.setKeyPool("openai", new KeyPool([process.env["OPENAI_API_KEY"]!]));

  const config = {
    provider: "openai" as const,
    model: "gpt-4.1-mini",
    common: { temperature: 0.2, maxOutputTokens: 128 },
    caching: { strategy: "redis" as const, ttlSeconds: 3600 },
  };

  const messages = [
    { role: "user" as const, content: "What is singleflight deduplication?" },
  ];

  // First call — cache miss, hits the provider
  const first = await pipeline.call({ config, messages });
  console.log(`[miss] ${first.content}`);
  console.log(`  cache_hit=${first.cacheHit}`);

  // Second call — same config + messages, should hit Redis L2
  const second = await pipeline.call({ config, messages });
  console.log(`\n[hit]  ${second.content}`);
  console.log(`  cache_hit=${second.cacheHit}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
