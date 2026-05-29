/**
 * Provider kwargs injection -- per-provider request mutation before dispatch.
 *
 * Each provider can define a `transformKwargs` callback that mutates request
 * parameters before the API call is made. This keeps provider-specific quirks
 * (reasoning model parameter stripping, default extra_body injection, etc.)
 * isolated from the main client code.
 *
 * Provider-specific behavior is isolated here so the main client dispatch path
 * can stay provider-neutral.
 */

import { getGpuBaseUrl } from "./env.js";
import { getModelCapabilities } from "./model-capabilities.js";
import type { ProviderOptions } from "./types.js";

const GPU_PATH_PATTERN = /^[a-zA-Z0-9_-]+(\/[a-zA-Z0-9_-]+)*$/;

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
 * Reasoning models (o-series, gpt-5*, codex-, computer-use-)
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
// OpenRouter: inject extra_body.provider.sort = "price"
// =============================================================================

const OPENROUTER_DEFAULT_PROVIDER = { sort: "price" } as const;

/**
 * Inject default provider sorting config for OpenRouter.
 * Sets extra_body.provider.sort = "price" when not already present.
 */
export function openrouterTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const result = { ...kwargs };
  const raw = result["extra_body"];
  const extraBody: Record<string, unknown> =
    typeof raw === "object" && raw !== null && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  const openrouterOptions = ctx.providerOptions?.openrouter;
  if (!("provider" in extraBody)) {
    extraBody["provider"] = openrouterOptions?.provider ?? OPENROUTER_DEFAULT_PROVIDER;
  }
  if (!("reasoning" in extraBody) && openrouterOptions?.reasoning !== undefined) {
    extraBody["reasoning"] = openrouterOptions.reasoning;
  }
  if (Object.keys(extraBody).length > 0) {
    result["extra_body"] = { ...extraBody };
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
// Sno GPU: construct base URL and carry thinking settings
// =============================================================================

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

/**
 * Construct base URL from providerOptions["sno-gpu"].gpuPath and surface
 * effective thinking settings so dispatchers can forward them consistently.
 *
 * Builds: {base}/{gpuPath}/v1 when gpuPath is present.
 * Falls back to {base}/v1 when gpuPath is absent.
 */
export function snoGpuTransformKwargs(
  ctx: TransformKwargsContext,
  kwargs: Record<string, unknown>
): Record<string, unknown> {
  const result = { ...kwargs };

  const snoGpuOpts = ctx.providerOptions?.["sno-gpu"];
  const gpuPath = snoGpuOpts?.gpuPath;

  let base = (ctx.baseUrl ?? "").replace(/\/+$/, "");
  if (!base) {
    base = (getGpuBaseUrl() ?? "").replace(/\/+$/, "");
  }
  if (base.endsWith("/v1")) {
    base = base.slice(0, -3);
  }

  if (!base) {
    throw new Error(
      "sno-gpu provider requires a non-empty base_url in config or GPU_BASE_URL env var",
    );
  }

  if (gpuPath) {
    // Validate gpuPath: length cap, no traversal, alphanumeric + hyphens/underscores/slashes only
    if (
      gpuPath.length > 256 ||
      gpuPath.includes("..") ||
      !GPU_PATH_PATTERN.test(gpuPath)
    ) {
      throw new Error(
        `Invalid gpu_path: ${JSON.stringify(gpuPath)}. Must be a safe relative path using letters, digits, "_", "-", or "/".`,
      );
    }
    result["baseUrl"] = `${base}/${gpuPath}/v1`;
  } else {
    result["baseUrl"] = `${base}/v1`;
  }

  const enableThinking =
    asBoolean(result["enableThinking"]) ??
    asBoolean(result["enable_thinking"]) ??
    snoGpuOpts?.enableThinking ??
    ctx.enableThinking;
  if (
    enableThinking !== undefined &&
    result["enableThinking"] === undefined &&
    result["enable_thinking"] === undefined
  ) {
    result["enableThinking"] = enableThinking;
  }

  const thinkingBudget =
    asNumber(result["thinkingBudget"]) ??
    asNumber(result["thinking_budget"]) ??
    snoGpuOpts?.thinkingBudget;
  if (
    thinkingBudget !== undefined &&
    result["thinkingBudget"] === undefined &&
    result["thinking_budget"] === undefined
  ) {
    result["thinkingBudget"] = thinkingBudget;
  }

  return result;
}

// =============================================================================
// Registry: provider name -> default callback
// =============================================================================

export const PROVIDER_KWARGS_REGISTRY: Record<string, TransformKwargsCallback> = {
  openai: openaiTransformKwargs,
  openrouter: openrouterTransformKwargs,
  // Legacy DeepSeek configs route through OpenRouter.
  deepseek: openrouterTransformKwargs,
  google: geminiTransformKwargs,
  gemini: geminiTransformKwargs,
  "sno-gpu": snoGpuTransformKwargs,
};
