/**
 * Provider kwargs injection -- per-provider request mutation before dispatch.
 *
 * Each provider can define a `transformKwargs` callback that mutates request
 * parameters before the API call is made. This keeps provider-specific quirks
 * (reasoning model parameter stripping, default extra_body injection, etc.)
 * isolated from the main client code.
 *
 * Ported from repo-reference/llm-provider/src/llm_provider/providers/_registry.py
 */

import { getModelCapabilities } from "./model-capabilities";
import type { ProviderOptions } from "./types";

// =============================================================================
// Types
// =============================================================================

export interface TransformKwargsContext {
  model: string;
  provider: string;
  messages?: unknown[];
  temperature?: number;
  topP?: number;
  providerOptions?: ProviderOptions;
  baseUrl?: string;
}

export type TransformKwargsCallback = (
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
) => Record<string, unknown>;

// =============================================================================
// Core dispatch
// =============================================================================

/**
 * Apply a provider's transformKwargs callback if non-null.
 * Returns kwargs unchanged when callback is undefined/null.
 */
export function applyTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>,
  callback: TransformKwargsCallback | undefined | null
): Record<string, unknown> {
  if (callback == null) {
    return kwargs;
  }
  return callback(ctx, kwargs);
}

// =============================================================================
// OpenAI: strip temperature / top_p for reasoning models
// =============================================================================

/**
 * Strip temperature and top_p for OpenAI reasoning models.
 *
 * Reasoning models (o-series, gpt-5 except gpt-5-chat, codex-, computer-use-)
 * require temperature=1 and do not accept top_p.
 */
export function openaiTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const capabilities = getModelCapabilities(ctx.model);
  if (!capabilities.isReasoningModel) {
    return kwargs;
  }

  const result = { ...kwargs };
  delete result.temperature;
  delete result.top_p;
  return result;
}

// =============================================================================
// OpenRouter (DeepSeek): inject extra_body.provider.sort = "price"
// =============================================================================

const OPENROUTER_DEFAULT_PROVIDER = { sort: "price" } as const;

/**
 * Inject default provider sorting config for OpenRouter.
 * Sets extra_body.provider.sort = "price" when not already present.
 */
export function openrouterTransformKwargs(
  _ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const result = { ...kwargs };
  const extraBody = (result.extra_body as Record<string, unknown>) ?? {};
  if (!("provider" in extraBody)) {
    result.extra_body = { ...extraBody, provider: OPENROUTER_DEFAULT_PROVIDER };
  }
  return result;
}

// =============================================================================
// Gemini: default thinking_budget=0, override from providerOptions
// =============================================================================

/**
 * Set default ThinkingConfig(thinkingBudget=0), override from providerOptions.
 *
 * Disables thinking by default. If providerOptions.google.thinkingBudget is
 * set, uses that value instead.
 */
export function geminiTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const result = { ...kwargs };

  const googleOpts = ctx.providerOptions?.google;
  const thinkingBudget: number = googleOpts?.thinkingConfig?.thinkingBudget ?? 0;

  const thinkingConfig = (result.thinkingConfig as Record<string, unknown>) ?? {};
  if (!("thinkingBudget" in thinkingConfig)) {
    result.thinkingConfig = { ...thinkingConfig, thinkingBudget };
  }

  return result;
}

// =============================================================================
// Sno GPU: construct base URL from providerOptions.snogpu.gpuPath
// =============================================================================

/**
 * Construct base URL from providerOptions.snogpu.gpuPath.
 *
 * Builds: {base}/{gpuPath}/v1 when gpuPath is present.
 * Falls back to {base}/v1 when gpuPath is absent.
 */
export function snogpuTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const result = { ...kwargs };

  // ProviderOptions type doesn't include snogpu in TS yet — access dynamically
  const providerOptions = ctx.providerOptions as Record<string, unknown> | undefined;
  const snogpuOpts = (providerOptions?.snogpu as Record<string, unknown>) ?? {};
  const gpuPath = snogpuOpts.gpuPath as string | undefined;

  let base = (ctx.baseUrl ?? "").replace(/\/+$/, "");
  if (base.endsWith("/v1")) {
    base = base.slice(0, -3);
  }

  // Skip URL modification if base is empty — would produce a relative path, not a valid URL
  if (!base) {
    return result;
  }

  if (gpuPath) {
    result.baseUrl = `${base}/${gpuPath}/v1`;
  } else {
    result.baseUrl = `${base}/v1`;
  }

  return result;
}

// =============================================================================
// Registry: provider name -> default callback
// =============================================================================

export const PROVIDER_KWARGS_REGISTRY: Record<string, TransformKwargsCallback> = {
  openai: openaiTransformKwargs,
  deepseek: openrouterTransformKwargs,
  google: geminiTransformKwargs,
  snogpu: snogpuTransformKwargs,
};
