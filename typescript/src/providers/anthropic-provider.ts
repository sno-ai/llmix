/**
 * Anthropic Provider for LLMix
 *
 * Creates Anthropic model instances via @ai-sdk/anthropic for use with
 * AI SDK v6 generateText()/streamText().
 *
 * Features:
 * - Model factory with API key and base URL configuration
 * - Native prompt caching via cache_control injection
 * - Extended thinking (budgeted thinking tokens) passthrough
 * - Helicone proxy routing support
 * - Provider options passthrough to AI SDK
 */

import type { LanguageModel } from "ai";
import type { AnthropicProviderOptions, CachingStrategy } from "../types";

let _anthropicPromise: Promise<typeof import("@ai-sdk/anthropic")> | null = null;

function getAnthropicSdk(): Promise<typeof import("@ai-sdk/anthropic")> {
  if (!_anthropicPromise) {
    _anthropicPromise = import("@ai-sdk/anthropic").catch((cause: unknown) => {
      _anthropicPromise = null; // allow retry on failure
      throw new Error(
        "Anthropic provider requires '@ai-sdk/anthropic'. Install with: bun add @ai-sdk/anthropic",
        { cause },
      );
    });
  }
  return _anthropicPromise;
}

/** Options for creating an Anthropic model instance */
export interface AnthropicModelOptions {
  /** Anthropic API key (falls back to ANTHROPIC_API_KEY env var) */
  apiKey?: string;

  /** Base URL override (e.g., for Helicone proxy) */
  baseURL?: string;

  /** Custom headers (e.g., Helicone headers) */
  headers?: Record<string, string>;

  /** Caching strategy */
  cachingStrategy?: CachingStrategy;

  /** Provider-specific options (thinking, cacheControl, etc.) */
  providerOptions?: AnthropicProviderOptions;
}

/**
 * Create an Anthropic model instance for use with AI SDK v6.
 *
 * @param modelId - Anthropic model ID (e.g., "claude-sonnet-4-20250514")
 * @param options - Model configuration options
 * @returns AI SDK LanguageModel instance
 *
 * @example
 * ```typescript
 * const model = createAnthropicModel("claude-sonnet-4-20250514", {
 *   apiKey: "sk-ant-...",
 *   cachingStrategy: "native",
 * });
 *
 * const result = await generateText({ model, messages: [...] });
 * ```
 */
export async function createAnthropicModel(
  modelId: string,
  options?: AnthropicModelOptions
): Promise<LanguageModel> {
  const { apiKey, baseURL, headers } = options ?? {};

  const { createAnthropic } = await getAnthropicSdk();
  const anthropic = createAnthropic({
    apiKey,
    baseURL,
    headers,
  });

  return anthropic(modelId) as LanguageModel;
}

/**
 * Build providerOptions for Anthropic calls via AI SDK v6.
 *
 * Maps LLMix AnthropicProviderOptions to the format expected by
 * AI SDK's generateText/streamText providerOptions.anthropic.
 *
 * @param options - LLMix Anthropic provider options
 * @param cachingStrategy - Active caching strategy
 * @returns Provider options object for AI SDK, or undefined if empty
 */
export function buildAnthropicProviderOptions(
  options?: AnthropicProviderOptions,
  cachingStrategy?: CachingStrategy
): Record<string, unknown> | undefined {
  if (!options && cachingStrategy !== "native") {
    return undefined;
  }

  const result: Record<string, unknown> = {};

  if (options?.thinking) {
    result.thinking = options.thinking;
  }

  if (options?.cacheControl || cachingStrategy === "native") {
    result.cacheControl = options?.cacheControl ?? { type: "ephemeral" };
  }

  if (options?.disableParallelToolUse !== undefined) {
    result.disableParallelToolUse = options.disableParallelToolUse;
  }

  if (options?.sendReasoning !== undefined) {
    result.sendReasoning = options.sendReasoning;
  }

  if (options?.effort !== undefined) {
    result.effort = options.effort;
  }

  if (options?.toolStreaming !== undefined) {
    result.toolStreaming = options.toolStreaming;
  }

  if (options?.structuredOutputMode !== undefined) {
    result.structuredOutputMode = options.structuredOutputMode;
  }

  return Object.keys(result).length > 0 ? result : undefined;
}
