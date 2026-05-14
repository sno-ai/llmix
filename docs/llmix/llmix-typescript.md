# LLMix TypeScript Guide

Read this after the README. This page is the application-code guide: it shows
how to install LLMix, run calls, publish the official registry, and open the
generated registry from a TypeScript service. Use the secure MDA guide for the
full production release runbook.

Install the package from npm:

```bash
npm install @snoai/llmix
```

This package also installs the official `llmix` command used by the secure
registry flow.

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

## Mental Model

LLMix has five pieces:

| Piece | What it does |
| --- | --- |
| `CallPipeline` | Runs one LLM call through cache, retries, key rotation, circuit breaker, singleflight, and dispatch. |
| `PipelineConfig` | Wires the dispatch function and runtime knobs. |
| `CallInput` | Carries the resolved model config and chat messages. |
| `KeyPool` | Rotates API keys per provider and marks dead keys on auth failures. |
| `TwoTierCache` | Uses in-process memory as L1 and optional Redis as L2. |

LLMix is not a replacement for OpenAI, Anthropic, AI SDK, LiteLLM, or your own
provider client. It wraps the call site where those SDKs are used.

## Config Shape

TypeScript uses camelCase in code:

```typescript
{
  provider: "openai",
  model: "gpt-4o-mini",
  common: { temperature: 0.2, maxOutputTokens: 512 },
  caching: { strategy: "memory", ttl: 3600 },
  providerOptions: {
    openai: { reasoningEffort: "medium" },
  },
}
```

The same preset in an `.mda` source also uses camelCase under
`metadata.snoai-llmix`. Python and Rust normalize known fields into their
snake_case runtime shape after loading.

## Provider Coverage

| Provider family | TypeScript helper |
| --- | --- |
| OpenAI-compatible | `openaiDispatch()` |
| Anthropic | `anthropicDispatch()` |
| Gemini | `geminiDispatch()` |
| OpenRouter | `openrouterDispatch()` |
| DeepInfra | `deepinfraDispatch()` |
| Novita | `novitaDispatch()` |
| Together | `togetherDispatch()` |
| SNO GPU | `snoGpuDispatch()` |

Provider helpers use optional SDKs. Install only the provider SDKs you call.

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

## Environment Variables

Key pools can be loaded from environment variables. Provider names normalize
hyphens to underscores and uppercase.

| Provider | Multi-key variable | Single-key fallback |
| --- | --- | --- |
| OpenAI | `OPENAI_KEYS` | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_KEYS` | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_KEYS` | `GEMINI_API_KEY` |
| OpenRouter | `OPENROUTER_KEYS` | `OPENROUTER_API_KEY` |
| DeepInfra | `DEEPINFRA_KEYS` | `DEEPINFRA_API_KEY` |
| Novita | `NOVITA_KEYS` | `NOVITA_API_KEY` |
| Together | `TOGETHER_KEYS` | `TOGETHER_API_KEY` |
| SNO GPU | `SNO_GPU_KEYS` | `SNO_GPU_API_KEY` |

`*_KEYS` is comma-separated. If both variables exist, `*_KEYS` wins.

## Config Registry

Production apps should publish MDA presets into the official registry layout:

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

`source/` is edited by people. `current.json` and `compiled/` are generated by
LLMix. Keep the trust anchor outside `config/llm`.

The example below uses one preset:

```text
config/llm/source/search_summary/openai_fast.mda
```

The did:web example assumes these release-identity inputs already exist outside
`config/llm`: `release/did-web-private-key.pem` and `release/did.json`. Provide
them from your release system or use the GitHub Actions Sigstore/Rekor profile
instead.

Create and gate the source preset with MDA CLI:

```bash
mkdir -p config/llm/source/search_summary release deploy

mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out config/llm/source/search_summary/openai_fast.mda

mda validate config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --json

mda integrity compute config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --write \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/trust-policy.json \
  --json

mda sign config/llm/source/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json

mda verify config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --json

mda release prepare \
  --target llmix-registry \
  --source config/llm/source \
  --registry-dir config/llm \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --out release/plan.json \
  --json
```

Publish the registry with LLMix, then finalize and doctor it with MDA CLI:

```bash
llmix publish-registry \
  --root config/llm \
  --release-plan release/plan.json \
  --revision 2026-05-14T000000Z \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --root-did did:web:config.example.com \
  --root-key-id did:web:config.example.com#release \
  --root-key-file release/did-web-private-key.pem \
  --json

mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/compiled/2026-05-14T000000Z/registry-root.json \
  --release-plan release/plan.json \
  --policy release/trust-policy.json \
  --derive-root-digest \
  --minimum-revision 2026-05-14T000000Z \
  --out deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

mda doctor release \
  --target llmix-registry \
  --source config/llm/source \
  --registry-dir config/llm \
  --release-plan release/plan.json \
  --manifest deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

llmix check-registry \
  --root config/llm \
  --trust deploy/llmix-trust.json \
  --preset search_summary/openai_fast \
  --did-document release/did.json \
  --tamper-proof \
  --json
```

Runtime code opens the generated registry with the external trust anchor:

```typescript
import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const trust = await loadLlmixTrustManifest(process.env.LLMIX_TRUST_ANCHOR!);
const manager = await ConfigRegistryManager.open("config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(trust, { didWebVerifier }),
});
const config = await manager.getPreset("search_summary", "openai_fast");
console.log(await manager.availablePresets());
```

`didWebVerifier` is the app verifier hook required by this did:web policy. For a
command-line runtime proof, use `llmix check-registry --did-document
release/did.json`; in app code, pass the verifier hooks required by your trust
policy.

This is the fixed split: MDA CLI gates the release before and after publishing,
LLMix publishes and loads the registry, and runtime code passes `signedRoot`
options derived from the external trust anchor.

`ConfigRegistryPublisher` is available as an advanced API when a release system
must call LLMix as a library. It is the same publisher behind
`llmix publish-registry`; it is not a custom compiler path.

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

const config = await loadMdaConfig("./config/llm/source/search_summary/openai_fast.mda");
const preset = await loadMdaConfigPreset("openai_fast", "./config/llm/source/search_summary");
```

For production runtime code, prefer `ConfigRegistryManager`.

## MDA Source Presets

MDA is the source format for presets. Use it when model choice, provider
options, cache policy, timeout policy, tags, and rollout metadata should be
reviewed as source files instead of hidden in application code.

The LLMix-specific data lives under `metadata.snoai-llmix`. MDA-owned mechanism
fields such as `requires`, `integrity`, and `signatures` stay at the top level
and are handled by the MDA parser.

```md
---
name: openai_fast
title: OpenAI Fast Search Summary
description: Fast OpenAI preset for search summaries.
tags:
  - search
  - production
requires:
  network: public
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 512
      maxRetries: 2
    providerOptions:
      openai:
        reasoningEffort: medium
        textVerbosity: low
    timeout:
      totalTime: 45
      streamFirstChunkTime: 12
    caching:
      strategy: redis-or-memory
      ttl: 3600
      maxItems: 2000
    tags:
      - search
      - production
---

Summarize search results for a research workflow.
```

`metadata.snoai-llmix` is strict. Unknown keys are rejected unless a field is
documented as a provider-specific pass-through record. Presets should fail
during publishing, not during a production request.

| Key | Required | Purpose |
| --- | --- | --- |
| `common` | yes | Provider, model, and portable generation parameters. |
| `providerOptions` | no | Provider-specific options such as OpenAI reasoning effort or Anthropic thinking. |
| `timeout` | no | Per-call timeout hints in seconds. |
| `description` | no | Overrides the top-level MDA description in the projected runtime config. |
| `deprecated` | no | Marks a preset as deprecated for tooling. |
| `tags` | no | Overrides top-level MDA tags in the projected runtime config. |
| `caching` | no | Response cache strategy and TTL. |
| `bypassGateway` | no | Compatibility flag for deployments that route around a gateway. |

`common.provider` and `common.model` are required. Supported providers are
`openai`, `anthropic`, `google`, `deepseek`, `openrouter`, `deepinfra`,
`novita`, `together`, and `sno-gpu`.

The reserved semantic payload type for signed LLMix presets is:

```text
application/vnd.snoai-llmix.preset+json
```

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

## Timeouts and Cancellation

`timeout.totalTime` in an LLMix config is a runtime budget for dispatch
implementations. The TypeScript `CallPipeline` currently passes the config into
your dispatch function, but it does not wrap dispatch with a hard network
timeout and it does not create an `AbortSignal` for you. The built-in
TypeScript dispatch helpers also do not currently enforce `timeout.totalTime`
as a transport-level abort.

Do not implement dispatch timeout with `Promise.race` alone. It only rejects
the caller-side promise; it does not cancel the provider request. Use a
provider-native timeout or pass an `AbortController.signal` into the actual
network call.

```typescript
import type { ProviderDispatchFn } from "@snoai/llmix";

const myDispatch: ProviderDispatchFn = async (ctx) => {
  const totalTimeMs =
    typeof ctx.config.timeout?.totalTime === "number"
      ? ctx.config.timeout.totalTime * 1000
      : 120_000;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), totalTimeMs);

  try {
    const response = await fetch("https://api.example.com/v1/chat", {
      method: "POST",
      signal: controller.signal,
      headers: {
        authorization: `Bearer ${ctx.apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: ctx.model,
        messages: ctx.messages,
        ...ctx.kwargs,
      }),
    });

    if (!response.ok) {
      throw new Error(`provider failed with ${response.status}`);
    }

    const result = await response.json() as { text: string };
    return {
      content: result.text,
      model: ctx.model,
      usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
    };
  } finally {
    clearTimeout(timer);
  }
};
```

This matters when retries are enabled. If a timed-out attempt is not aborted at
the transport layer, the next retry can create a second concurrent provider
request while the previous one is still running.

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

## Boundaries

Good fits for LLMix:

- shared model presets
- cache policy
- retry and concurrency defaults
- API key rotation
- provider kwargs normalization

Keep these in product code:

- user authorization
- billing policy
- product-specific prompt branching
- provider account ownership rules
