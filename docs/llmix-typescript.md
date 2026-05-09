# LLMix TypeScript Guide

Install the package from npm:

```bash
npm install @snoai/llmix
```

or with Bun:

```bash
bun add @snoai/llmix
```

Provider helpers use optional peer dependencies. Install the provider SDK you
actually call:

```bash
npm install ai @ai-sdk/openai
```

For Redis-backed response cache:

```bash
npm install ioredis
```

## Quick Start

```typescript
import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
} from "@snoai/llmix";

async function main(): Promise<void> {
  const pipeline = new CallPipeline({
    dispatch: openaiDispatch(),
    responseCache: new TwoTierCache("memory"),
  });
  pipeline.setKeyPool("openai", new KeyPool([process.env["OPENAI_API_KEY"]!]));

  const response = await pipeline.call({
    config: {
      provider: "openai",
      model: "gpt-4o-mini",
      common: { temperature: 0.7, maxOutputTokens: 256 },
      caching: { strategy: "memory" },
    },
    messages: [
      { role: "user", content: "In one sentence, what is LLMix?" },
    ],
  });

  console.log(response.content);
  console.log(`cache_hit=${response.cacheHit}`);
  await pipeline.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

Run it:

```bash
OPENAI_API_KEY=sk-... node quickstart.js
```

With TypeScript directly:

```bash
OPENAI_API_KEY=sk-... bun run quickstart.ts
```

## Redis Cache

```typescript
import { PipelineConfig, TwoTierCache, openaiDispatch } from "@snoai/llmix";

const config: PipelineConfig = {
  dispatch: openaiDispatch(),
  responseCache: new TwoTierCache("redis-or-memory", {
    redisUrl: process.env["REDIS_URL"],
    maxItems: 2048,
    ttlSeconds: 3600,
  }),
};
```

Use `"redis"` when Redis is required. Use `"redis-or-memory"` when local memory
fallback is acceptable.

## Key Pools

```typescript
import { KeyPool, loadKeysFromEnv } from "@snoai/llmix";

pipeline.setKeyPool("openai", loadKeysFromEnv("openai"));

// Or explicitly:
pipeline.setKeyPool("openai", new KeyPool(["sk-live-1", "sk-live-2"]));
```

`loadKeysFromEnv("openai")` checks `OPENAI_KEYS` first, then
`OPENAI_API_KEY`. `OPENAI_KEYS` is comma-separated.

## Config Registry

```typescript
import {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  resolveConfigDir,
} from "@snoai/llmix";

const { configDir } = resolveConfigDir();
await new ConfigRegistryPublisher(configDir).publish();

const manager = await ConfigRegistryManager.open(configDir);
const config = await manager.getPreset("search", "summary");
console.log(await manager.availablePresets());
```

Use the resolved config in a call:

```typescript
const response = await pipeline.call({
  config,
  messages: [{ role: "user", content: "Summarize this." }],
});
```

## Direct MDA Loading

```typescript
import { loadMdaConfig, loadMdaConfigPreset } from "@snoai/llmix";

const config = await loadMdaConfig("./config/llm/search/summary.mda");
const preset = await loadMdaConfigPreset("summary", "./config/llm/search");
```

For production runtime code, prefer `ConfigRegistryManager`.

## Custom Dispatch

```typescript
import type { ProviderDispatchFn } from "@snoai/llmix";

const myDispatch: ProviderDispatchFn = async (ctx) => {
  const result = await myClient.chat({
    model: ctx.model,
    messages: ctx.messages,
    apiKey: ctx.apiKey,
    ...ctx.kwargs,
  });

  return {
    content: result.text,
    model: ctx.model,
    usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
  };
};

const pipeline = new CallPipeline({ dispatch: myDispatch });
```

## Public Runtime Knobs

```typescript
const pipeline = new CallPipeline({
  dispatch: openaiDispatch(),
  maxRetries: 3,
  retryBaseMs: 1000,
  retryMaxDelayMs: 30000,
  circuitBreakerThreshold: 3,
  circuitBreakerCooldownMs: 30_000,
  semaphoreInitial: 32,
  semaphoreMin: 4,
  responseCache: new TwoTierCache("memory"),
});
```

Most services should start with defaults. Tune only after real traffic shows a
specific pressure point.
