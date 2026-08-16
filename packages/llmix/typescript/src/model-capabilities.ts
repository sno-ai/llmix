/**
 * Model Capabilities - Provider-specific parameter filtering
 *
 * Different model families support different parameters. This module provides
 * capability detection and parameter filtering to prevent API errors when
 * sending unsupported parameters.
 *
 * Logic mirrors @ai-sdk/openai's internal implementation:
 * @see node_modules/@ai-sdk/openai/dist/index.js → isReasoningModel(), getResponsesModelConfig()
 *
 * Model Classes (from AI SDK source):
 * - Reasoning: Models matching /^o\d/ (o1, o3, o4...), "gpt-5*", "codex-", "computer-use"
 * - Standard: Everything else (gpt-4, gpt-4o, gpt-4.1, claude, gemini, etc.)
 *
 * Parameter Support:
 * - reasoningEffort: Only reasoning models (AI SDK validates this client-side)
 * - textVerbosity: Only GPT-5 series (OpenAI API rejects for other models)
 * - temperature: Fixed at 1 for reasoning models
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/openai
 */

import capabilityRules from "../data/model-capabilities.json" with { type: "json" };
import { stripVendorPrefix } from "./model-id.js";
import type { OpenAIProviderOptions } from "./types.js";

/**
 * Model capability flags
 */
export interface ModelCapabilities {
  /** Is this a reasoning model (o-series, gpt-5, codex) */
  isReasoningModel: boolean;
  /** Supports textVerbosity parameter (GPT-5 only) */
  supportsTextVerbosity: boolean;
  /** Temperature is fixed at 1 (reasoning models) */
  fixedTemperature: boolean;
  /** Model class for logging */
  modelClass: "gpt5" | "o-series" | "codex" | "standard";
}

/**
 * Classification rules, compiled once from data/model-capabilities.json.
 *
 * `^o\d` is narrower than the AI SDK's `startsWith("o")` on purpose: it matches
 * o1/o3/o4 but not opus, omni, or orca.
 *
 * `fixedTemperature` is deliberately a separate list from `reasoningModel`.
 * Being a reasoning model and being forbidden to send a temperature are
 * different facts — the temperature restriction belongs to the OpenAI
 * families, and a reasoning model from another vendor keeps its temperature.
 */
const REASONING_PATTERNS = capabilityRules.reasoningModelPrefixes.map((p) => new RegExp(p));
const TEXT_VERBOSITY_PATTERNS = capabilityRules.textVerbosityPrefixes.map((p) => new RegExp(p));
const FIXED_TEMPERATURE_PATTERNS = capabilityRules.fixedTemperaturePrefixes.map(
  (p) => new RegExp(p)
);
const MODEL_CLASS_PATTERNS = capabilityRules.modelClassRules.map((rule) => ({
  pattern: new RegExp(rule.prefix),
  class: rule.class as ModelCapabilities["modelClass"],
}));
const DEFAULT_MODEL_CLASS = capabilityRules.defaultModelClass as ModelCapabilities["modelClass"];

function matchesAny(patterns: readonly RegExp[], normalizedId: string): boolean {
  return patterns.some((pattern) => pattern.test(normalizedId));
}

/**
 * Detect model capabilities based on model ID
 *
 * Rules come from data/model-capabilities.json, which Python consumes too.
 * The id is normalized first so a gateway-addressed model
 * (`openai/gpt-5.6-luna`) classifies identically to its bare form.
 */
export function getModelCapabilities(modelId: string): ModelCapabilities {
  const normalized = stripVendorPrefix(modelId);

  const matchedClass = MODEL_CLASS_PATTERNS.find(({ pattern }) => pattern.test(normalized));

  return {
    isReasoningModel: matchesAny(REASONING_PATTERNS, normalized),
    supportsTextVerbosity: matchesAny(TEXT_VERBOSITY_PATTERNS, normalized),
    fixedTemperature: matchesAny(FIXED_TEMPERATURE_PATTERNS, normalized),
    modelClass: matchedClass?.class ?? DEFAULT_MODEL_CLASS,
  };
}

/**
 * Parameters that were filtered out (for logging)
 */
export interface FilteredParams {
  reasoningEffort?: string;
  textVerbosity?: string;
  temperature?: number;
}

/**
 * Filter OpenAI provider options based on model capabilities
 *
 * Strips unsupported parameters to prevent API errors.
 * Returns both filtered options and what was removed (for logging).
 *
 * Note: AI SDK already validates reasoningEffort client-side for non-reasoning models.
 * We still filter it here as a safety net and to provide consistent warnings.
 */
export function filterOpenAIProviderOptions(
  modelId: string,
  options: OpenAIProviderOptions | undefined
): {
  filteredOptions: OpenAIProviderOptions | undefined;
  filteredParams: FilteredParams;
  capabilities: ModelCapabilities;
} {
  const capabilities = getModelCapabilities(modelId);

  if (!options) {
    return {
      filteredOptions: undefined,
      filteredParams: {},
      capabilities,
    };
  }

  const filteredParams: FilteredParams = {};
  const filteredOptions = { ...options };

  // Filter reasoningEffort for non-reasoning models
  // AI SDK already validates this, but we filter as safety net
  if (!capabilities.isReasoningModel && filteredOptions.reasoningEffort) {
    filteredParams.reasoningEffort = filteredOptions.reasoningEffort;
    delete filteredOptions.reasoningEffort;
  }

  // Filter textVerbosity for models that don't support it
  // This is NOT validated by AI SDK - OpenAI API returns error
  if (!capabilities.supportsTextVerbosity && filteredOptions.textVerbosity) {
    filteredParams.textVerbosity = filteredOptions.textVerbosity;
    delete filteredOptions.textVerbosity;
  }

  return {
    filteredOptions: Object.keys(filteredOptions).length > 0 ? filteredOptions : undefined,
    filteredParams,
    capabilities,
  };
}

/**
 * Check if temperature needs adjustment for reasoning models
 *
 * Reasoning models (o-series, GPT-5) require temperature=1.
 * Returns the adjusted temperature and whether it was changed.
 */
export function adjustTemperatureForModel(
  modelId: string,
  temperature: number | undefined
): {
  adjustedTemperature: number | undefined;
  wasAdjusted: boolean;
  originalTemperature?: number;
} {
  const capabilities = getModelCapabilities(modelId);

  // If model has fixed temperature and user specified non-1 temperature
  if (capabilities.fixedTemperature && temperature !== undefined && temperature !== 1) {
    return {
      adjustedTemperature: 1,
      wasAdjusted: true,
      originalTemperature: temperature,
    };
  }

  return {
    adjustedTemperature: temperature,
    wasAdjusted: false,
  };
}
