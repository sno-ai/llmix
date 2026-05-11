/**
 * LLMix TypeScript graceful degradation pattern.
 *
 * Implements a fallback chain: primary model → cheaper model →
 * cached response → static fallback. Maintains service availability
 * during provider outages.
 *
 * Run with:
 *   OPENAI_API_KEY=sk-... bun run examples/llmix/typescript/graceful-degradation.ts
 */

import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
  CircuitOpenError,
  TimeoutError,
  ProviderError,
  type CallInput,
  type CallResponse,
} from "@snoai/llmix";

interface FallbackLevel {
  name: string;
  config: CallInput["config"];
}

const FALLBACK_CHAIN: FallbackLevel[] = [
  {
    name: "primary (gpt-4.1)",
    config: {
      provider: "openai",
      model: "gpt-4.1",
      common: { temperature: 0.5, maxOutputTokens: 256 },
      caching: { strategy: "redis" as const, ttlSeconds: 3600 },
    },
  },
  {
    name: "degraded (gpt-4.1-mini)",
    config: {
      provider: "openai",
      model: "gpt-4.1-mini",
      common: { temperature: 0.5, maxOutputTokens: 256 },
      caching: { strategy: "memory" as const },
    },
  },
];

const STATIC_FALLBACK = "I'm currently unable to process your request. Please try again shortly.";

async function callWithFallback(
  pipeline: CallPipeline,
  messages: CallInput["messages"],
): Promise<{ response: string; level: string }> {
  for (const level of FALLBACK_CHAIN) {
    try {
      const result: CallResponse = await pipeline.call({
        config: level.config,
        messages,
      });
      return { response: result.content, level: level.name };
    } catch (error: unknown) {
      const isRecoverable =
        error instanceof CircuitOpenError ||
        error instanceof TimeoutError ||
        (error instanceof ProviderError && error.statusCode >= 500);

      if (!isRecoverable) throw error;
      console.log(`  [FALLBACK] ${level.name} failed, trying next level...`);
    }
  }

  // All providers failed — try cache-only lookup
  const cached = await pipeline.cacheLookup({ config: FALLBACK_CHAIN[0].config, messages });
  if (cached) {
    return { response: cached.content, level: "stale-cache" };
  }

  return { response: STATIC_FALLBACK, level: "static-fallback" };
}

async function main(): Promise<void> {
  const pipeline = new CallPipeline({
    dispatch: openaiDispatch(),
    responseCache: new TwoTierCache("memory"),
  });
  pipeline.setKeyPool("openai", new KeyPool([process.env["OPENAI_API_KEY"]!]));

  const messages = [{ role: "user" as const, content: "What is LLMix?" }];
  const { response, level } = await callWithFallback(pipeline, messages);

  console.log(`Response (${level}): ${response}`);
}

main().catch(console.error);
