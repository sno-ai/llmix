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
 * - Reasoning: Models starting with "o", "gpt-5", "codex-", "computer-use" (except gpt-5-chat)
 * - Standard: Everything else (gpt-4, gpt-4o, gpt-4.1, claude, gemini, etc.)
 *
 * Parameter Support:
 * - reasoningEffort: Only reasoning models (AI SDK validates this client-side)
 * - textVerbosity: Only GPT-5 series (OpenAI API rejects for other models)
 * - temperature: Fixed at 1 for reasoning models
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/openai
 */

import type { OpenAIProviderOptions } from "./types";

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
 * Check if model is a reasoning model
 *
 * Mirrors AI SDK's isReasoningModel():
 * ```js
 * return (modelId.startsWith("o") || modelId.startsWith("gpt-5")) && !modelId.startsWith("gpt-5-chat");
 * ```
 *
 * Extended to also include codex- and computer-use- prefixes from getResponsesModelConfig()
 */
function isReasoningModel(modelId: string): boolean {
  const lower = modelId.toLowerCase();
  // gpt-5-chat is explicitly NOT a reasoning model
  if (lower.startsWith("gpt-5-chat")) return false;
  // Reasoning models: o*, gpt-5*, codex-*, computer-use-*
  return (
    lower.startsWith("o") ||
    lower.startsWith("gpt-5") ||
    lower.startsWith("codex-") ||
    lower.startsWith("computer-use")
  );
}

/**
 * Check if model supports textVerbosity
 *
 * Currently only GPT-5 series supports textVerbosity.
 * o-series and other reasoning models do NOT support it.
 */
function supportsTextVerbosity(modelId: string): boolean {
  const lower = modelId.toLowerCase();
  // Only gpt-5 series (not gpt-5-chat) supports textVerbosity
  return lower.startsWith("gpt-5") && !lower.startsWith("gpt-5-chat");
}

/**
 * Detect model capabilities based on model ID
 *
 * Uses same logic as AI SDK's internal implementation.
 */
export function getModelCapabilities(modelId: string): ModelCapabilities {
  const lower = modelId.toLowerCase();
  const reasoning = isReasoningModel(modelId);

  // Determine model class for logging
  let modelClass: ModelCapabilities["modelClass"] = "standard";
  if (lower.startsWith("gpt-5") && !lower.startsWith("gpt-5-chat")) {
    modelClass = "gpt5";
  } else if (lower.startsWith("o")) {
    modelClass = "o-series";
  } else if (lower.startsWith("codex-") || lower.startsWith("computer-use")) {
    modelClass = "codex";
  }

  return {
    isReasoningModel: reasoning,
    supportsTextVerbosity: supportsTextVerbosity(modelId),
    fixedTemperature: reasoning,
    modelClass,
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
