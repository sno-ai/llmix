/**
 * LLMClient - Unified LLM Interface with Config-Driven Calls
 *
 * Provides a unified interface for making LLM calls using config from LLMConfigLoader.
 * Direct AI SDK v6 mapping - no parameter renaming.
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
import { filterOpenAIProviderOptions } from "./model-capabilities";
import type {
  CachingConfig,
  CachingStrategy,
  CallOptions,
  ConfigCapabilities,
  LLMCallEventData,
  LLMixTelemetryProvider,
  LLMResponse,
  LLMUsage,
  OpenAIProviderOptions,
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
 * Helicone configuration for native prompt caching
 *
 * Used when caching.strategy = "native" for OpenAI calls.
 * Helicone proxies OpenAI requests and enables 90% cost savings on cached tokens.
 */
export interface HeliconeConfig {
  /** Helicone API key (required for native caching) */
  apiKey?: string;
  /** Helicone base URL (default: https://oai.helicone.ai/v1) */
  baseUrl?: string;
}

/**
 * API keys configuration for LLM providers
 * Allows injecting API keys instead of relying on process.env
 */
export interface ApiKeysConfig {
  /** OpenAI API key */
  openai?: string;
  /** Anthropic API key */
  anthropic?: string;
  /** Google Generative AI API key */
  google?: string;
  /** OpenRouter API key (used for DeepSeek) */
  openrouter?: string;
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

  /**
   * Helicone configuration for native prompt caching
   * Required for caching.strategy = "native" with OpenAI
   */
  helicone?: HeliconeConfig;

  /**
   * API keys for LLM providers
   * If not provided, falls back to environment variables
   */
  apiKeys?: ApiKeysConfig;

  /**
   * Enable telemetry payload capture for debugging
   * When true, full messages/output are included in telemetry
   * Default: false (env fallback: LLMIX_CAPTURE_TELEMETRY_PAYLOAD)
   */
  captureTelemetryPayload?: boolean;

  /**
   * Call timeout in milliseconds
   * Prevents hanging requests from tying up resources
   * Default: 120000 (env fallback: LLMIX_CALL_TIMEOUT_MS)
   */
  callTimeoutMs?: number;
}

/**
 * Parsed profile result
 */
interface ParsedProfile {
  module: string;
  profile: string;
}

/**
 * AI SDK v6 usage format
 * Token details are now nested under inputTokenDetails/outputTokenDetails
 */
interface AISDKUsage {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  /** Input token details (v6 structure) */
  inputTokenDetails?: {
    /** Cached input tokens (prompt caching) - was cachedInputTokens in v5 */
    cacheReadTokens?: number;
  };
  /** Output token details (v6 structure) */
  outputTokenDetails?: {
    /** Reasoning tokens (o-series models) - was reasoningTokens in v5 */
    reasoningTokens?: number;
  };
}

// =============================================================================
// CONSTANTS
// =============================================================================

/** Models that support OpenAI Batch API */
const BATCH_CAPABLE_MODEL_PATTERNS = [/^gpt-4/, /^gpt-5/, /^o1/, /^o3/];

/** Default Helicone base URL for OpenAI proxy */
const HELICONE_OPENAI_BASE_URL = "https://oai.helicone.ai/v1";

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
 * Check if a model is an embedding model
 */
function isEmbeddingModel(model: string): boolean {
  return model.toLowerCase().includes("embedding");
}

/**
 * Resolve effective caching strategy from config and overrides
 *
 * Priority: override.bypassGateway (legacy) > config.caching > config.bypassGateway (legacy) > default
 *
 * @param config - Resolved LLM config
 * @param overrideBypassGateway - Legacy override flag
 * @returns Effective caching config
 */
function resolveCachingStrategy(
  config: ResolvedLLMConfig,
  overrideBypassGateway?: boolean
): CachingConfig {
  // Priority 1: Legacy override.bypassGateway (backwards compat)
  if (overrideBypassGateway !== undefined) {
    console.warn(
      `[LLMix] DEPRECATED: bypassGateway override used. Use caching.strategy instead.`
    );
    return overrideBypassGateway
      ? { strategy: "native", key: config.caching?.key }
      : { strategy: "gateway" };
  }

  // Priority 2: Config caching (new system)
  if (config.caching) {
    return config.caching;
  }

  // Priority 3: Legacy config.bypassGateway (backwards compat)
  if (config.bypassGateway !== undefined) {
    console.warn(
      `[LLMix] DEPRECATED: bypassGateway config used in ${config.configId}. Use caching.strategy instead.`
    );
    return config.bypassGateway ? { strategy: "native" } : { strategy: "gateway" };
  }

  // Default: gateway caching (backwards compat with existing behavior)
  return { strategy: "gateway" };
}

/**
 * Routing options for getProviderModel
 */
interface ProviderRoutingOptions {
  /** Provider URL config for CF AI Gateway */
  urls?: ProviderUrlConfig;
  /** API keys (falls back to process.env) */
  apiKeys?: ApiKeysConfig;
  /** Helicone config for native caching */
  helicone?: HeliconeConfig;
  /** Caching strategy */
  cachingStrategy?: CachingStrategy;
  /** Cache key for native strategy */
  cacheKey?: string;
}

/**
 * Get provider model instance for AI SDK v6
 *
 * LH: Added CF AI Gateway support via baseURL configuration.
 * LH: Added Helicone routing for native prompt caching.
 *
 * Routing logic:
 * - strategy="native" + OpenAI + LLM: Route via Helicone (https://oai.helicone.ai/v1)
 * - strategy="native" + OpenAI + embedding: Direct OpenAI (native caching not supported)
 * - strategy="gateway": Use CF AI Gateway URLs
 * - strategy="disabled": Direct provider URLs
 *
 * @param provider - Provider name
 * @param model - Model ID
 * @param options - Routing options (URLs, API keys, caching)
 * @returns AI SDK model instance with appropriate headers
 */
function getProviderModel(
  provider: Provider,
  model: string,
  options?: ProviderRoutingOptions
): LanguageModel {
  const { urls, apiKeys, helicone, cachingStrategy, cacheKey } = options ?? {};

  // LH: Log routing decision
  if (cachingStrategy === "native") {
    console.debug(`[LLMix] Using native caching for provider: ${provider}`);
  } else if (urls?.useCfAiGateway && cachingStrategy === "gateway") {
    console.debug(`[LLMix] Using CF AI Gateway for provider: ${provider}`);
  } else if (cachingStrategy === "disabled") {
    console.debug(`[LLMix] Caching disabled for provider: ${provider}`);
  }

  switch (provider) {
    case "openai": {
      const apiKey = apiKeys?.openai ?? process.env.OPENAI_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] OPENAI_API_KEY environment variable is required for OpenAI provider"
        );
      }

      // LH: Native caching for OpenAI LLM calls (not embeddings)
      if (cachingStrategy === "native" && !isEmbeddingModel(model)) {
        const heliconeApiKey = helicone?.apiKey ?? process.env.HELICONE_API_KEY;
        if (!heliconeApiKey) {
          console.warn(
            `[LLMix] Native caching requested but HELICONE_API_KEY not set. Falling back to gateway.`
          );
          // Fall through to gateway logic
        } else {
          const heliconeBaseUrl = helicone?.baseUrl ?? HELICONE_OPENAI_BASE_URL;
          console.log(
            `[LLMix] Routing OpenAI via Helicone for native prompt caching (key: ${cacheKey ?? "auto"})`
          );

          // Create OpenAI client with Helicone proxy and auth header
          const openai = createOpenAI({
            apiKey,
            baseURL: heliconeBaseUrl,
            headers: {
              "Helicone-Auth": `Bearer ${heliconeApiKey}`,
              // LH: Add cache key header if provided for prompt grouping
              ...(cacheKey && { "Helicone-Cache-Key": cacheKey }),
            },
          });
          return openai(model);
        }
      }

      // Gateway or disabled: use CF AI Gateway or direct
      const baseURL = cachingStrategy === "disabled" ? undefined : urls?.openaiBaseUrl;
      const openai = createOpenAI({ apiKey, baseURL });
      return openai(model);
    }
    case "anthropic": {
      const apiKey = apiKeys?.anthropic ?? process.env.ANTHROPIC_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] ANTHROPIC_API_KEY environment variable is required for Anthropic provider"
        );
      }
      // LH: Native caching for Anthropic uses direct API (Anthropic's built-in caching)
      // Gateway or disabled: use CF AI Gateway or direct
      const baseURL = cachingStrategy === "disabled" ? undefined : urls?.anthropicBaseUrl;
      const anthropic = createAnthropic({ apiKey, baseURL });
      return anthropic(model);
    }
    case "google": {
      const apiKey = apiKeys?.google ?? process.env.GOOGLE_GENERATIVE_AI_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] GOOGLE_GENERATIVE_AI_API_KEY environment variable is required for Google provider"
        );
      }
      // Gateway or disabled: use CF AI Gateway or direct
      // Note: geminiBaseUrl includes /v1beta suffix when using CF AI Gateway
      const baseURL = cachingStrategy === "disabled" ? undefined : urls?.geminiBaseUrl;
      const google = createGoogleGenerativeAI({ apiKey, baseURL });
      return google(model);
    }
    case "deepseek": {
      // LH: Route DeepSeek models through OpenRouter for better reliability
      // OpenRouter provides unified access, automatic failover, and better rate limit handling
      // Uses @ai-sdk/openai since OpenRouter is OpenAI-compatible (no need for dedicated provider)
      const apiKey = apiKeys?.openrouter ?? urls?.openRouterApiKey ?? process.env.OPENROUTER_API_KEY;
      if (!apiKey) {
        throw new Error(
          "[LLMix] OPENROUTER_API_KEY environment variable is required for DeepSeek provider (via OpenRouter)"
        );
      }

      // Map model name to OpenRouter format (provider/model)
      const openRouterModel = DEEPSEEK_MODEL_MAPPINGS[model] ?? `deepseek/${model}`;

      // Base URL: CF Gateway (if not disabled) > env > default OpenRouter
      const baseURL =
        cachingStrategy === "disabled"
          ? "https://openrouter.ai/api/v1"
          : urls?.openRouterBaseUrl ?? "https://openrouter.ai/api/v1";

      // LH: Log routing info
      if (urls?.useCfAiGateway && urls.openRouterBaseUrl && cachingStrategy !== "disabled") {
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
 * LH: Now extracts cachedInputTokens for Anthropic prompt caching
 */
function extractUsage(usage: AISDKUsage | undefined): LLMUsage {
  const inputTokens = usage?.inputTokens ?? 0;
  const outputTokens = usage?.outputTokens ?? 0;
  // v6: cachedInputTokens moved to inputTokenDetails.cacheReadTokens
  const cachedInputTokens = usage?.inputTokenDetails?.cacheReadTokens;
  return {
    inputTokens,
    outputTokens,
    totalTokens: usage?.totalTokens ?? inputTokens + outputTokens,
    cachedInputTokens: cachedInputTokens ?? undefined,
  };
}

// =============================================================================
// LLM CLIENT CLASS
// =============================================================================

/**
 * LLM Client for making config-driven LLM calls
 *
 * Uses LLMConfigLoader for configuration resolution and AI SDK v6 for LLM calls.
 */
export class LLMClient {
  private readonly loader: LLMConfigLoader;
  private readonly defaultScope?: string;
  private readonly telemetry?: LLMixTelemetryProvider;
  private readonly providerUrls?: ProviderUrlConfig;
  private readonly helicone?: HeliconeConfig;
  private readonly apiKeys?: ApiKeysConfig;
  private readonly captureTelemetryPayload: boolean;
  private readonly callTimeoutMs: number;

  constructor(config: LLMClientConfig) {
    this.loader = config.loader;
    this.defaultScope = config.defaultScope;
    this.telemetry = config.telemetry;
    this.providerUrls = config.providerUrls;
    this.helicone = config.helicone;
    this.apiKeys = config.apiKeys;
    // Config takes precedence, then env var, then default
    this.captureTelemetryPayload =
      config.captureTelemetryPayload ?? process.env.LLMIX_CAPTURE_TELEMETRY_PAYLOAD === "true";
    this.callTimeoutMs =
      config.callTimeoutMs ?? (Number(process.env.LLMIX_CALL_TIMEOUT_MS) || 120000);
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

    // LH: Compute effective timeout with fallback chain:
    // profile.timeout.totalTime (minutes) → clientConfig.callTimeoutMs (ms) → 120000ms
    const effectiveTimeoutMs = config.timeout?.totalTime
      ? config.timeout.totalTime * 60 * 1000 // Convert minutes to ms
      : this.callTimeoutMs;

    try {
      // LH: Resolve caching strategy from config and overrides
      const cachingConfig = resolveCachingStrategy(config, options.overrides?.bypassGateway);
      const { strategy: cachingStrategy, key: configCacheKey } = cachingConfig;

      // LH: Use promptCacheKey from call options (takes priority over config key)
      const effectiveCacheKey = options.promptCacheKey ?? configCacheKey;

      // Log caching strategy
      console.log(
        `[LLMix] Caching strategy: ${cachingStrategy} for ${options.profile}${effectiveCacheKey ? ` (key: ${effectiveCacheKey})` : ""}`
      );

      // Get provider model instance with caching configuration
      const model = getProviderModel(config.provider, effectiveModel, {
        urls: this.providerUrls,
        apiKeys: this.apiKeys,
        helicone: this.helicone,
        cachingStrategy,
        cacheKey: effectiveCacheKey,
      });

      // LH: Filter provider options based on model capabilities
      // This prevents errors like "textVerbosity not supported with gpt-4.1"
      let finalProviderOptions = effectiveProviderOptions;
      if (config.provider === "openai" && effectiveProviderOptions?.openai) {
        const { filteredOptions, filteredParams, capabilities } = filterOpenAIProviderOptions(
          effectiveModel,
          effectiveProviderOptions.openai as OpenAIProviderOptions
        );

        // Log filtered params as warnings (helps debug config issues)
        if (Object.keys(filteredParams).length > 0) {
          console.warn(
            `[LLMix] Filtered unsupported params for ${effectiveModel} (${capabilities.modelClass}):`,
            filteredParams
          );
        }

        // Update provider options with filtered version
        finalProviderOptions = filteredOptions
          ? { ...effectiveProviderOptions, openai: filteredOptions }
          : { ...effectiveProviderOptions, openai: undefined };
      }

      // LH: Setup abort controller for timeout handling
      const abortController = new AbortController();
      const timeoutId = setTimeout(() => {
        abortController.abort();
      }, effectiveTimeoutMs);

      // Build generateText options - direct AI SDK v6 mapping
      // Use type assertion for flexibility with different message formats
      const generateOptions = {
        model,
        messages: options.messages,
        // Spread common params directly (AI SDK v6 compatible)
        ...effectiveCommon,
        // Add provider-specific options if present (after capability filtering)
        ...(finalProviderOptions?.[config.provider] && {
          providerOptions: {
            [config.provider]: finalProviderOptions[config.provider],
          },
        }),
        // LH: Add abort signal for timeout handling
        abortSignal: abortController.signal,
      };

      // Make the LLM call (assertion needed for AI SDK type flexibility)
      // LH: Log model info for debugging/A/B test verification
      console.log(
        `[LLMix] Calling ${config.provider}/${effectiveModel} (profile: ${options.profile}, version: ${config.version})`
      );

      let result;
      try {
        result = await generateText(generateOptions as Parameters<typeof generateText>[0]);
      } finally {
        clearTimeout(timeoutId);
      }

      // Extract usage
      const usage = extractUsage(result.usage as AISDKUsage | undefined);
      const latencyMs = Date.now() - startTime;

      // LH: Log prompt cache status for production debugging (visible in Docker logs)
      // Only log for prompts that could be cached (>1024 tokens)
      if (usage.inputTokens >= 1024) {
        if (usage.cachedInputTokens && usage.cachedInputTokens > 0) {
          const cacheHitPercent = Math.round((usage.cachedInputTokens / usage.inputTokens) * 100);
          const tokensSaved = usage.cachedInputTokens;
          console.log(
            `[LLMix] CACHE HIT | profile=${options.profile} | model=${effectiveModel} | ` +
              `cached=${tokensSaved}/${usage.inputTokens} (${cacheHitPercent}%) | latency=${latencyMs}ms`
          );
        } else if (cachingStrategy === "native") {
          // Only log cache miss for native caching strategy (expecting prompt cache)
          console.log(
            `[LLMix] CACHE MISS | profile=${options.profile} | model=${effectiveModel} | ` +
              `tokens=${usage.inputTokens} | latency=${latencyMs}ms | (first request or cache expired)`
          );
        }
      }

      // LH: Track telemetry fire-and-forget with timeout (non-blocking)
      // Prevents slow telemetry from blocking user responses
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
      const isTimeoutError = isAbortError && latencyMs >= effectiveTimeoutMs - 100;

      const baseErrorMessage = error instanceof Error ? error.message : String(error);
      const errorMessage = isTimeoutError
        ? `[LLMix] Request timeout after ${effectiveTimeoutMs}ms: ${baseErrorMessage}`
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
          timeoutMs: effectiveTimeoutMs,
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
    // Set captureTelemetryPayload=true or LLMIX_CAPTURE_TELEMETRY_PAYLOAD=true to include full payloads
    const redactedMessages = this.captureTelemetryPayload
      ? messages
      : [{ redacted: true, count: messages.length }];
    const redactedOutput = this.captureTelemetryPayload ? output : output ? "[redacted]" : undefined;

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
