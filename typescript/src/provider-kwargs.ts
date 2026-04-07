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
  messages?: unknown[] | undefined;
  temperature?: number | undefined;
  topP?: number | undefined;
  providerOptions?: ProviderOptions | undefined;
  baseUrl?: string | undefined;
  enableThinking?: boolean | undefined;
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
  delete result["temperature"];
  delete result["top_p"];

  // Reasoning models use max_completion_tokens, not max_tokens
  if (result["max_tokens"] !== undefined && result["max_completion_tokens"] === undefined) {
    result["max_completion_tokens"] = result["max_tokens"];
    delete result["max_tokens"];
  }

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
  const raw = result["extra_body"];
  const extraBody: Record<string, unknown> =
    typeof raw === "object" && raw !== null && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  if (!("provider" in extraBody)) {
    result["extra_body"] = { ...extraBody, provider: OPENROUTER_DEFAULT_PROVIDER };
  }
  return result;
}

// =============================================================================
// Gemini: default thinking_budget=0, override from providerOptions
// =============================================================================

/**
 * Set default ThinkingConfig(thinkingBudget=0), override from providerOptions.
 *
 * Disables thinking by default. When ctx.enableThinking is true and no explicit
 * budget is provided, skips injecting a budget so the provider uses its own default.
 * If providerOptions.google.thinkingConfig.thinkingBudget is set, uses that value.
 */
export function geminiTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const result = { ...kwargs };

  const googleOpts = ctx.providerOptions?.google;
  const explicitBudget = googleOpts?.thinkingConfig?.thinkingBudget;
  // When enableThinking is true and no explicit budget, let the provider decide
  const thinkingBudget: number | undefined =
    explicitBudget ?? (ctx.enableThinking ? undefined : 0);

  const rawTc = result["thinkingConfig"];
  const thinkingConfig: Record<string, unknown> =
    typeof rawTc === "object" && rawTc !== null && !Array.isArray(rawTc)
      ? (rawTc as Record<string, unknown>)
      : {};
  if (!("thinkingBudget" in thinkingConfig) && thinkingBudget !== undefined) {
    result["thinkingConfig"] = { ...thinkingConfig, thinkingBudget };
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

  const snogpuOpts = ctx.providerOptions?.snogpu;
  const gpuPath = snogpuOpts?.gpuPath;

  let base = (ctx.baseUrl ?? "").replace(/\/+$/, "");
  if (base.endsWith("/v1")) {
    base = base.slice(0, -3);
  }

  if (!base) {
    throw new Error("snogpu provider requires a non-empty baseUrl");
  }

  if (gpuPath) {
    // Validate gpuPath: no traversal, alphanumeric + hyphens/underscores/slashes only
    if (gpuPath.includes("..") || !/^[a-zA-Z0-9_/-]+$/.test(gpuPath)) {
      throw new Error(`Invalid gpuPath: "${gpuPath}"`);
    }
    result["baseUrl"] = `${base}/${gpuPath}/v1`;
  } else {
    result["baseUrl"] = `${base}/v1`;
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
  gemini: geminiTransformKwargs,
  snogpu: snogpuTransformKwargs,
};
