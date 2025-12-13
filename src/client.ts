/**
 * LLMClient - Unified LLM Interface with Config-Driven Calls
 *
 * Provides a unified interface for making LLM calls using config from LLMConfigLoader.
 * Direct AI SDK v5 mapping - no parameter renaming.
 *
 * Features:
 * - Profile string parsing ("module:profile" or "profile")
 * - Multi-provider support (OpenAI, Anthropic, Google, DeepSeek)
 * - Optional telemetry via dependency injection
 * - Runtime overrides with config merging
 * - Capability detection for batch API support
 *
 * @example
 * ```typescript
 * const client = createLLMClient({ loader });
 *
 * const response = await client.call({
 *   profile: 'hrkg:extraction',
 *   messages: [{ role: 'user', content: 'Hello' }],
 * });
 * ```
 */

import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createOpenAI } from "@ai-sdk/openai";
import type { LanguageModel } from "ai";
import { generateText } from "ai";
import type { LLMConfigLoader } from "./config-loader";
import type {
  CallOptions,
  ConfigCapabilities,
  LLMCallEventData,
  LLMixTelemetryProvider,
  LLMResponse,
  LLMUsage,
  Provider,
  ResolvedConfigResult,
  ResolvedLLMConfig,
  TelemetryContext,
} from "./types";

// =============================================================================
// TYPES
// =============================================================================

/**
 * Configuration for LLMClient
 */
export interface LLMClientConfig {
  /** LLMConfigLoader instance for loading configs */
  loader: LLMConfigLoader;

  /** Default scope for config resolution (default: uses loader's defaultScope) */
  defaultScope?: string;

  /**
   * Optional telemetry provider for tracking LLM calls
   *
   * If not provided, telemetry is disabled (no external dependencies).
   * Inject your implementation to integrate with PostHog, Langfuse, etc.
   */
  telemetry?: LLMixTelemetryProvider;
}

/**
 * Parsed profile result
 */
interface ParsedProfile {
  module: string;
  profile: string;
}

/**
 * AI SDK v5 usage format
 * Handles different field naming between providers
 */
interface AISDKUsage {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
}

// =============================================================================
// CONSTANTS
// =============================================================================

/** Models that support OpenAI Batch API */
const BATCH_CAPABLE_MODEL_PATTERNS = [/^gpt-4/, /^gpt-5/, /^o1/, /^o3/];

/** DeepSeek API base URL */
const DEEPSEEK_BASE_URL = "https://api.deepseek.com";

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

/**
 * Parse profile string into module and profile
 *
 * @param profileString - "module:profile" or "profile"
 * @returns Parsed module and profile
 *
 * @example
 * parseProfile("hrkg:extraction") // { module: "hrkg", profile: "extraction" }
 * parseProfile("extraction") // { module: "_default", profile: "extraction" }
 */
function parseProfile(profileString: string): ParsedProfile {
  const colonIndex = profileString.indexOf(":");
  if (colonIndex === -1) {
    return { module: "_default", profile: profileString };
  }
  return {
    module: profileString.slice(0, colonIndex),
    profile: profileString.slice(colonIndex + 1),
  };
}

/**
 * Check if a model supports OpenAI Batch API
 */
function isBatchCapable(model: string): boolean {
  return BATCH_CAPABLE_MODEL_PATTERNS.some((pattern) => pattern.test(model));
}

/**
 * Get provider model instance for AI SDK v5
 *
 * @param provider - Provider name
 * @param model - Model ID
 * @returns AI SDK model instance
 */
function getProviderModel(provider: Provider, model: string): LanguageModel {
  switch (provider) {
    case "openai": {
      const apiKey = process.env.OPENAI_API_KEY;
      if (!apiKey) {
        throw new Error("OPENAI_API_KEY environment variable is required");
      }
      const openai = createOpenAI({ apiKey });
      return openai(model);
    }
    case "anthropic": {
      // Anthropic provider requires @ai-sdk/anthropic package
      // Currently not installed - throw clear error
      throw new Error(
        "Anthropic provider requires @ai-sdk/anthropic package. " +
          "Install with: bun add @ai-sdk/anthropic"
      );
    }
    case "google": {
      const apiKey = process.env.GOOGLE_GENERATIVE_AI_API_KEY;
      if (!apiKey) {
        throw new Error("GOOGLE_GENERATIVE_AI_API_KEY environment variable is required");
      }
      const google = createGoogleGenerativeAI({ apiKey });
      return google(model);
    }
    case "deepseek": {
      const apiKey = process.env.DEEPSEEK_API_KEY;
      if (!apiKey) {
        throw new Error("DEEPSEEK_API_KEY environment variable is required");
      }
      // DeepSeek uses OpenAI-compatible API
      const deepseek = createOpenAI({ apiKey, baseURL: DEEPSEEK_BASE_URL });
      return deepseek(model);
    }
  }
}

/**
 * Derive capabilities from resolved config
 */
function deriveCapabilities(config: ResolvedLLMConfig): ConfigCapabilities {
  return {
    provider: config.provider,
    // All supported providers are proprietary
    isProprietary: true,
    // Only OpenAI with batch-capable models supports batch API
    supportsOpenAIBatch: config.provider === "openai" && isBatchCapable(config.model),
  };
}

/**
 * Extract usage from AI SDK response
 */
function extractUsage(usage: AISDKUsage | undefined): LLMUsage {
  const inputTokens = usage?.promptTokens ?? 0;
  const outputTokens = usage?.completionTokens ?? 0;
  return {
    inputTokens,
    outputTokens,
    totalTokens: usage?.totalTokens ?? inputTokens + outputTokens,
    cachedInputTokens: undefined,
  };
}


// =============================================================================
// LLM CLIENT CLASS
// =============================================================================

/**
 * LLM Client for making config-driven LLM calls
 *
 * Uses LLMConfigLoader for configuration resolution and AI SDK v5 for LLM calls.
 */
export class LLMClient {
  private readonly loader: LLMConfigLoader;
  private readonly defaultScope?: string;
  private readonly telemetry?: LLMixTelemetryProvider;

  constructor(config: LLMClientConfig) {
    this.loader = config.loader;
    this.defaultScope = config.defaultScope;
    this.telemetry = config.telemetry;
  }

  /**
   * Make an LLM call using resolved config
   *
   * @param options - Call options including profile, messages, and overrides
   * @returns LLM response with content, usage, and config metadata
   *
   * @example
   * ```typescript
   * const response = await client.call({
   *   profile: 'hrkg:extraction',
   *   messages: [{ role: 'user', content: 'Extract entities from: ...' }],
   *   overrides: { common: { temperature: 0.5 } },
   * });
   * ```
   */
  async call(options: CallOptions): Promise<LLMResponse> {
    const startTime = Date.now();

    // Parse profile string
    const { module, profile } = parseProfile(options.profile);

    // Load config via loader
    const config = await this.loader.loadConfig({
      scope: options.scope ?? this.defaultScope,
      module,
      profile,
      userId: options.userId,
      version: options.version,
    });

    // Apply runtime overrides
    const effectiveModel = options.overrides?.model ?? config.model;
    const effectiveCommon = {
      ...config.common,
      ...options.overrides?.common,
    };
    const effectiveProviderOptions = {
      ...config.providerOptions,
      ...options.overrides?.providerOptions,
    };

    try {
      // Get provider model instance
      const model = getProviderModel(config.provider, effectiveModel);

      // Build generateText options - direct AI SDK v5 mapping
      // Use type assertion for flexibility with different message formats
      const generateOptions = {
        model,
        messages: options.messages,
        // Spread common params directly (AI SDK v5 compatible)
        ...effectiveCommon,
        // Add provider-specific options if present
        ...(effectiveProviderOptions?.[config.provider] && {
          providerOptions: {
            [config.provider]: effectiveProviderOptions[config.provider],
          },
        }),
      };

      // Make the LLM call (assertion needed for AI SDK type flexibility)
      const result = await generateText(generateOptions as Parameters<typeof generateText>[0]);

      // Extract usage
      const usage = extractUsage(result.usage as AISDKUsage | undefined);

      // Track telemetry (best-effort - don't fail successful LLM calls)
      const latencyMs = Date.now() - startTime;
      try {
        await this.trackTelemetry({
          config,
          effectiveModel,
          usage,
          latencyMs,
          success: true,
          messages: options.messages,
          output: result.text,
          telemetryContext: options.telemetry,
        });
      } catch (telemetryError) {
        // Log but don't fail the successful LLM call
        console.warn(
          `[LLMClient] Telemetry failed for ${config.configId}: ${String(telemetryError)}`
        );
      }

      return {
        content: result.text,
        model: effectiveModel,
        provider: config.provider,
        usage,
        config,
        success: true,
      };
    } catch (error) {
      const latencyMs = Date.now() - startTime;
      const errorMessage = error instanceof Error ? error.message : String(error);

      // Track failed call telemetry (best-effort)
      try {
        await this.trackTelemetry({
          config,
          effectiveModel,
          usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
          latencyMs,
          success: false,
          errorMessage,
          messages: options.messages,
          telemetryContext: options.telemetry,
        });
      } catch {
        // Ignore telemetry errors in failure path
      }

      return {
        content: "",
        model: effectiveModel,
        provider: config.provider,
        usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        config,
        success: false,
        error: errorMessage,
      };
    }
  }

  /**
   * Get resolved config and capabilities without making a call
   *
   * Useful for checking model capabilities before deciding how to process.
   *
   * @param options - Config resolution options (same as call() minus messages)
   * @returns Resolved config and capabilities
   *
   * @example
   * ```typescript
   * const { config, capabilities } = await client.getResolvedConfig({
   *   profile: 'hrkg:topic-analysis',
   * });
   *
   * if (capabilities.supportsOpenAIBatch) {
   *   // Use batch API for efficiency
   * }
   * ```
   */
  async getResolvedConfig(options: Omit<CallOptions, "messages">): Promise<ResolvedConfigResult> {
    // Parse profile string
    const { module, profile } = parseProfile(options.profile);

    // Load config via loader
    const config = await this.loader.loadConfig({
      scope: options.scope ?? this.defaultScope,
      module,
      profile,
      userId: options.userId,
      version: options.version,
    });

    // Derive capabilities
    const capabilities = deriveCapabilities(config);

    return { config, capabilities };
  }

  /**
   * Track telemetry for LLM call via injected provider
   *
   * No-op if telemetry provider not configured.
   */
  private async trackTelemetry(params: {
    config: ResolvedLLMConfig;
    effectiveModel: string;
    usage: LLMUsage;
    latencyMs: number;
    success: boolean;
    errorMessage?: string;
    messages: unknown[];
    output?: string;
    telemetryContext?: TelemetryContext;
  }): Promise<void> {
    // Skip if no telemetry provider configured
    if (!this.telemetry) {
      return;
    }

    const {
      config,
      effectiveModel,
      usage,
      latencyMs,
      success,
      errorMessage,
      messages,
      output,
      telemetryContext,
    } = params;

    // Build event data
    const event: LLMCallEventData = {
      configId: config.configId,
      provider: config.provider,
      model: effectiveModel,
      module: config.module,
      profile: config.profile,
      scope: config.scope,
      version: config.version,
      inputTokens: usage.inputTokens,
      outputTokens: usage.outputTokens,
      totalTokens: usage.totalTokens,
      latencyMs,
      success,
      errorMessage,
      context: telemetryContext,
      messages,
      output,
    };

    // Call injected provider
    await this.telemetry.trackLLMCall(event);
  }
}

// =============================================================================
// FACTORY FUNCTION
// =============================================================================

/**
 * Create a new LLMClient instance
 *
 * @param config - Client configuration
 * @returns New LLMClient instance
 *
 * @example
 * ```typescript
 * const loader = createLLMConfigLoader({ configDir: '/app/config/llm' });
 * await loader.init();
 *
 * const client = createLLMClient({ loader });
 *
 * const response = await client.call({
 *   profile: 'hrkg:extraction',
 *   messages: [{ role: 'user', content: 'Hello' }],
 * });
 * ```
 */
export function createLLMClient(config: LLMClientConfig): LLMClient {
  return new LLMClient(config);
}
