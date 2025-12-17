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

import { createAnthropic } from "@ai-sdk/anthropic";
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
 * Provider base URL configuration for CF AI Gateway support
 */
export interface ProviderUrlConfig {
  /** OpenAI base URL (for CF AI Gateway) */
  openaiBaseUrl?: string;
  /** Anthropic base URL (for CF AI Gateway) */
  anthropicBaseUrl?: string;
  /** Google/Gemini base URL (for CF AI Gateway) */
  geminiBaseUrl?: string;
  /** OpenRouter base URL (for CF AI Gateway) */
  openRouterBaseUrl?: string;
  /** OpenRouter API key (falls back to env var) */
  openRouterApiKey?: string;
  /** Whether CF AI Gateway is enabled (for debug logging) */
  useCfAiGateway?: boolean;
}

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

  /**
   * Provider URL configuration for CF AI Gateway support
   * If not provided, uses provider defaults (direct API calls)
   */
  providerUrls?: ProviderUrlConfig;
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

/**
 * LH: DeepSeek model mappings for OpenRouter
 * Maps config model names to OpenRouter format (provider/model)
 */
const DEEPSEEK_MODEL_MAPPINGS: Record<string, string> = {
  "deepseek-chat": "deepseek/deepseek-chat-v3-0324",
  "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
  "deepseek-v3.2-speciale": "deepseek/deepseek-chat-v3-0324:free", // Use free tier for speciale
  "deepseek-reasoner": "deepseek/deepseek-reasoner",
};

/**
 * LH: Telemetry payload capture control
 * Set LLMIX_CAPTURE_TELEMETRY_PAYLOAD=true to include full messages/output in telemetry
 * Default: false (redacted for privacy/PII protection)
 */
const CAPTURE_TELEMETRY_PAYLOAD = process.env.LLMIX_CAPTURE_TELEMETRY_PAYLOAD === "true";

/**
 * LH: Default timeout for LLM calls (in milliseconds)
 * Set LLMIX_CALL_TIMEOUT_MS to override (default: 120000 = 2 minutes)
 * Prevents hanging requests from tying up resources indefinitely
 */
const DEFAULT_CALL_TIMEOUT_MS = Number(process.env.LLMIX_CALL_TIMEOUT_MS) || 120000;

/**
 * LH: Telemetry timeout (in milliseconds)
 * Prevents slow telemetry from blocking responses
 * Default: 2000ms (2 seconds)
 */
const TELEMETRY_TIMEOUT_MS = 2000;

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
 * LH: Added CF AI Gateway support via baseURL configuration.
 * Base URLs are passed via ProviderUrlConfig which handles:
 * - Provider-specific gateway URLs
 * - Graceful fallback to undefined (uses provider defaults)
 *
 * @param provider - Provider name
 * @param model - Model ID
 * @param urls - Optional provider URL config for CF AI Gateway
 * @returns AI SDK model instance
 */
function getProviderModel(
  provider: Provider,
  model: string,
  urls?: ProviderUrlConfig
): LanguageModel {
  // LH: Log when CF AI Gateway is being used (debug level)
  if (urls?.useCfAiGateway) {
    console.debug(`[LLMix] Using CF AI Gateway for provider: ${provider}`);
  }

  switch (provider) {
    case "openai": {
      const apiKey = process.env.OPENAI_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] OPENAI_API_KEY environment variable is required for OpenAI provider"
        );
      }
      // LH: Pass baseURL for CF AI Gateway support (undefined = provider default)
      const openai = createOpenAI({ apiKey, baseURL: urls?.openaiBaseUrl });
      return openai(model);
    }
    case "anthropic": {
      const apiKey = process.env.ANTHROPIC_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] ANTHROPIC_API_KEY environment variable is required for Anthropic provider"
        );
      }
      // LH: Pass baseURL for CF AI Gateway support (undefined = provider default)
      const anthropic = createAnthropic({ apiKey, baseURL: urls?.anthropicBaseUrl });
      return anthropic(model);
    }
    case "google": {
      const apiKey = process.env.GOOGLE_GENERATIVE_AI_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] GOOGLE_GENERATIVE_AI_API_KEY environment variable is required for Google provider"
        );
      }
      // LH: Pass baseURL for CF AI Gateway support (undefined = provider default)
      // Note: geminiBaseUrl includes /v1beta suffix when using CF AI Gateway
      const google = createGoogleGenerativeAI({ apiKey, baseURL: urls?.geminiBaseUrl });
      return google(model);
    }
    case "deepseek": {
      // LH: Route DeepSeek models through OpenRouter for better reliability
      // OpenRouter provides unified access, automatic failover, and better rate limit handling
      // Uses @ai-sdk/openai since OpenRouter is OpenAI-compatible (no need for dedicated provider)
      const apiKey = urls?.openRouterApiKey ?? process.env.OPENROUTER_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] OPENROUTER_API_KEY environment variable is required for DeepSeek provider (via OpenRouter)"
        );
      }

      // Map model name to OpenRouter format (provider/model)
      const openRouterModel = DEEPSEEK_MODEL_MAPPINGS[model] ?? `deepseek/${model}`;

      // Base URL: CF Gateway > env > default OpenRouter
      const baseURL = urls?.openRouterBaseUrl ?? "https://openrouter.ai/api/v1";

      // LH: Log routing info
      if (urls?.useCfAiGateway && urls.openRouterBaseUrl) {
        console.debug(
          `[LLMix] Routing DeepSeek "${model}" via OpenRouter (CF Gateway) as "${openRouterModel}"`
        );
      } else {
        console.debug(
          `[LLMix] Routing DeepSeek "${model}" via OpenRouter (direct) as "${openRouterModel}"`
        );
      }

      const openrouter = createOpenAI({
        apiKey,
        baseURL,
      });
      return openrouter(openRouterModel);
    }
    default: {
      // LH: Explicit guard against unsupported providers at runtime
      // This should never happen if types are correct, but guards against bad config
      const exhaustiveCheck: never = provider;
      throw new Error(
        `[LLMix] Unsupported provider: ${exhaustiveCheck}. Supported: openai, anthropic, google, deepseek`
      );
    }
  }
}

/**
 * Derive capabilities from resolved config
 *
 * @param config - Resolved LLM config
 * @param effectiveModel - The actual model that will be used (after overrides)
 */
function deriveCapabilities(
  config: ResolvedLLMConfig,
  effectiveModel?: string
): ConfigCapabilities {
  const model = effectiveModel ?? config.model;
  return {
    provider: config.provider,
    // All supported providers are proprietary
    isProprietary: true,
    // Only OpenAI with batch-capable models supports batch API
    supportsOpenAIBatch: config.provider === "openai" && isBatchCapable(model),
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
  private readonly providerUrls?: ProviderUrlConfig;

  constructor(config: LLMClientConfig) {
    this.loader = config.loader;
    this.defaultScope = config.defaultScope;
    this.telemetry = config.telemetry;
    this.providerUrls = config.providerUrls;
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

    // LH: Wrap config loading in try/catch for consistent error response
    let config: ResolvedLLMConfig;
    try {
      config = await this.loader.loadConfig({
        scope: options.scope ?? this.defaultScope,
        module,
        profile,
        userId: options.userId,
        version: options.version,
      });
    } catch (configError) {
      const latencyMs = Date.now() - startTime;
      const errorMessage = configError instanceof Error ? configError.message : String(configError);
      const errorStack = configError instanceof Error ? configError.stack : undefined;

      // Log config load failure with context
      console.error(`[LLMix] Config load failed for profile ${options.profile}`, {
        error: errorMessage,
        stack: errorStack,
        module,
        profile,
        scope: options.scope ?? this.defaultScope,
        latencyMs,
      });

      // LH: Return consistent error response with proper types for error case
      // provider: "unknown" and config: undefined are type-safe for config load failures
      return {
        content: "",
        model: options.overrides?.model ?? "unknown",
        provider: "unknown", // Config load failed, provider is unknown
        usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        config: undefined, // No config available on config load failure
        success: false,
        error: `[LLMix] Config load failed: ${errorMessage}`,
      };
    }

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
      const model = getProviderModel(config.provider, effectiveModel, this.providerUrls);

      // LH: Setup abort controller for timeout handling
      const abortController = new AbortController();
      const timeoutId = setTimeout(() => {
        abortController.abort();
      }, DEFAULT_CALL_TIMEOUT_MS);

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
        // LH: Add abort signal for timeout handling
        abortSignal: abortController.signal,
      };

      // Make the LLM call (assertion needed for AI SDK type flexibility)
      let result;
      try {
        result = await generateText(generateOptions as Parameters<typeof generateText>[0]);
      } finally {
        clearTimeout(timeoutId);
      }

      // Extract usage
      const usage = extractUsage(result.usage as AISDKUsage | undefined);

      // LH: Track telemetry fire-and-forget with timeout (non-blocking)
      // Prevents slow telemetry from blocking user responses
      const latencyMs = Date.now() - startTime;
      void this.trackTelemetryNonBlocking({
        config,
        effectiveModel,
        usage,
        latencyMs,
        success: true,
        messages: options.messages,
        output: result.text,
        telemetryContext: options.telemetry,
      });

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

      // LH: Detect timeout/abort errors for clearer messaging
      const isAbortError =
        error instanceof Error &&
        (error.name === "AbortError" || error.message.includes("aborted"));
      const isTimeoutError = isAbortError && latencyMs >= DEFAULT_CALL_TIMEOUT_MS - 100;

      const baseErrorMessage = error instanceof Error ? error.message : String(error);
      const errorMessage = isTimeoutError
        ? `[LLMix] Request timeout after ${DEFAULT_CALL_TIMEOUT_MS}ms: ${baseErrorMessage}`
        : baseErrorMessage;
      const errorStack = error instanceof Error ? error.stack : undefined;

      // LH: Log error with full context for debugging (never fail silently)
      console.error(
        `[LLMix] LLM call failed for ${config.configId} (${config.provider}/${effectiveModel})`,
        {
          error: errorMessage,
          stack: errorStack,
          profile: options.profile,
          latencyMs,
          provider: config.provider,
          model: effectiveModel,
          isTimeout: isTimeoutError,
          timeoutMs: DEFAULT_CALL_TIMEOUT_MS,
        }
      );

      // LH: Track failed call telemetry fire-and-forget (non-blocking)
      void this.trackTelemetryNonBlocking({
        config,
        effectiveModel,
        usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        latencyMs,
        success: false,
        errorMessage,
        messages: options.messages,
        telemetryContext: options.telemetry,
      });

      return {
        content: "",
        model: effectiveModel,
        provider: config.provider,
        usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        config,
        success: false,
        error: errorMessage,
        // LH: Preserve stack trace for debugging (not exposed in LLMResponse type but useful for internal logging)
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

    // LH: Apply overrides to compute effective model for accurate capability detection
    const effectiveModel = options.overrides?.model ?? config.model;

    // Derive capabilities using effective model (after overrides)
    const capabilities = deriveCapabilities(config, effectiveModel);

    return { config, capabilities };
  }

  /**
   * LH: Non-blocking telemetry wrapper with timeout
   *
   * Fire-and-forget pattern: tracks telemetry without blocking responses.
   * Logs failures but never throws.
   */
  private async trackTelemetryNonBlocking(params: {
    config?: ResolvedLLMConfig;
    effectiveModel: string;
    usage: LLMUsage;
    latencyMs: number;
    success: boolean;
    errorMessage?: string;
    messages: unknown[];
    output?: string;
    telemetryContext?: TelemetryContext;
  }): Promise<void> {
    // Skip if no telemetry provider or no config (config load failure)
    if (!this.telemetry || !params.config) {
      return;
    }

    try {
      // Race telemetry against timeout to prevent blocking
      const timeoutPromise = new Promise<void>((_, reject) => {
        setTimeout(() => reject(new Error("Telemetry timeout")), TELEMETRY_TIMEOUT_MS);
      });

      await Promise.race([
        this.trackTelemetry(params as Parameters<typeof this.trackTelemetry>[0]),
        timeoutPromise,
      ]);
    } catch (error) {
      // Log but never throw - telemetry should not affect response
      console.warn(
        `[LLMix] Telemetry failed for ${params.config?.configId ?? "unknown"}: ${String(error)}`
      );
    }
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

    // LH: Redact messages/output by default for privacy (PII protection)
    // Set LLMIX_CAPTURE_TELEMETRY_PAYLOAD=true to include full payloads
    const redactedMessages = CAPTURE_TELEMETRY_PAYLOAD
      ? messages
      : [{ redacted: true, count: messages.length }];
    const redactedOutput = CAPTURE_TELEMETRY_PAYLOAD ? output : output ? "[redacted]" : undefined;

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
      messages: redactedMessages,
      output: redactedOutput,
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
