/**
 * LLMix Types
 *
 * Type definitions for the LLM Config Loader package.
 * Schema mirrors AI SDK v6 exactly - no parameter renaming required.
 *
 * @see https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text
 */

// =============================================================================
// CACHING STRATEGY
// =============================================================================

/**
 * Caching strategy for LLM calls
 *
 * - "native": Provider's native caching (OpenAI/Anthropic prompt caching via Helicone)
 *   - 90% cost savings on cached tokens
 *   - Requires cache key to group related prompts
 *   - Routes through Helicone for OpenAI (http://sno-main-1:8585)
 *   - Only for LLM calls (not embeddings)
 *
 * - "gateway": AI Gateway response caching (CF AI Gateway)
 *   - Exact match only
 *   - Good for identical requests
 *   - Works for all providers
 *
 * - "disabled": No caching
 *   - Always fresh calls
 *   - Useful for real-time or non-repeatable prompts
 */
export type CachingStrategy =
  | "native"
  | "gateway"
  | "disabled"
  | "redis"
  | "redis-or-memory"
  | "memory";

/** Strategies that activate the two-tier response cache. */
export type ResponseCacheStrategy = "redis" | "redis-or-memory" | "memory";

/** Cache hit tier indicator. */
export type CacheHitTier = "l1" | "l2";

/**
 * Caching configuration
 */
export interface CachingConfig {
  /** Caching strategy */
  strategy: CachingStrategy;

  /**
   * Cache key for native strategy
   *
   * Required for native strategy - groups related prompts together.
   * Optional for gateway/disabled strategies.
   *
   * Example: "extraction-v1", "search-2024"
   */
  key?: string | undefined;

  /** TTL in seconds for response cache entries (default: 3600). Applies to L1 and L2. */
  ttl?: number | undefined;

  /** Maximum L1 cache entries (default: 1000). */
  maxItems?: number | undefined;
}

// =============================================================================
// CONFIGURATION
// =============================================================================

/**
 * Configuration for LLMConfigLoader
 *
 * @example
 * ```typescript
 * const config: LLMConfigLoaderConfig = {
 *   configDir: '/app/config/llm',
 *   redisUrl: 'redis://localhost:6379',
 *   cacheSize: 100,
 *   cacheTtlSeconds: 21600,
 * };
 * ```
 */
export interface LLMConfigLoaderConfig {
  /** Base directory for LLM config files (required) */
  configDir: string;

  /** Redis URL - optional, works without Redis */
  redisUrl?: string | undefined;

  /** LRU cache max size (default: 100) */
  cacheSize?: number | undefined;

  /** Local cache TTL in seconds (default: 21600 = 6 hours) */
  cacheTtlSeconds?: number | undefined;

  /** Redis cache TTL in seconds (default: 86400 = 24 hours) */
  redisTtlSeconds?: number | undefined;

  /** Redis connection timeout in ms (default: 5000) */
  redisConnectTimeoutMs?: number | undefined;

  /** Redis command timeout in ms (default: 5000) */
  redisCommandTimeoutMs?: number | undefined;

  /** Max retries per Redis request (default: 3) */
  redisMaxRetries?: number | undefined;

  /** Default scope for config resolution (default: "default") */
  defaultScope?: string | undefined;

  /** Custom logger - uses console if not provided */
  logger?: LLMConfigLoaderLogger | undefined;
}

// =============================================================================
// LOGGER INTERFACE
// =============================================================================

/**
 * Logger interface for LLMConfigLoader
 *
 * Compatible with console, pino, winston, etc.
 */
export interface LLMConfigLoaderLogger {
  debug(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
  warn(message: string, ...args: unknown[]): void;
  error(message: string, ...args: unknown[]): void;
}

// =============================================================================
// TIMEOUT CONFIGURATION
// =============================================================================

/**
 * Timeout configuration for LLM calls (all values in seconds)
 *
 * Per-preset timeout allows reasoning models to have longer timeouts
 * than fast models, without affecting global defaults.
 *
 * @example
 * ```yaml
 * # In LLM config YAML
 * timeout:
 *   totalTime: 900  # 15 minutes for reasoning models
 *   streamFirstChunkTime: 120  # 2 minutes to first chunk
 * ```
 */
export interface TimeoutConfig {
  /**
   * Total time limit for the entire LLM call (seconds)
   *
   * After this time, the request is aborted.
   * Default: 120 (seconds)
   */
  totalTime?: number | undefined;

  /**
   * Max wait time for first chunk in streaming responses (seconds)
   *
   * For streaming calls, this limits how long to wait for the first token.
   * Useful for detecting slow/stuck models early.
   * Default: undefined (uses totalTime)
   */
  streamFirstChunkTime?: number | undefined;
}

// =============================================================================
// LLM CONFIG SCHEMA - AI SDK V6 ALIGNED
// =============================================================================

/**
 * Supported LLM providers
 */
export type Provider = "openai" | "anthropic" | "google" | "deepseek";

/** Provider type with unknown for error cases (config load failures) */
export type ProviderOrUnknown = Provider | "unknown";

/**
 * Common AI SDK v6 parameters
 *
 * These map directly to generateText/streamText params.
 * @see https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text
 */
export interface CommonParams {
  /** Max tokens to generate */
  maxOutputTokens?: number | undefined;

  /** Temperature 0.0-2.0 (don't use with topP) */
  temperature?: number | undefined;

  /** Top-p sampling 0.0-1.0 (don't use with temperature) */
  topP?: number | undefined;

  /** Sample from top K options */
  topK?: number | undefined;

  /** Reduce repetition of existing info */
  presencePenalty?: number | undefined;

  /** Reduce reuse of identical phrases */
  frequencyPenalty?: number | undefined;

  /** Sequences that halt generation */
  stopSequences?: string[] | undefined;

  /** Seed for deterministic results */
  seed?: number | undefined;

  /** Retry attempts (default: 2) */
  maxRetries?: number | undefined;

  /** Enable/disable thinking mode (provider-specific, e.g. Qwen3 on DeepInfra) */
  enableThinking?: boolean | undefined;

  /**
   * Preserve <think>...</think> blocks in response content.
   * When true, thinking blocks are NOT stripped and thinkingContent is not populated.
   * Default: false (thinking blocks are stripped automatically).
   */
  keepThinkingOutput?: boolean | undefined;
}

// =============================================================================
// PROVIDER-SPECIFIC OPTIONS - AI SDK V6 ALIGNED
// =============================================================================

/**
 * OpenAI-specific provider options
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/openai
 */
export interface OpenAIProviderOptions {
  /** Reasoning effort for reasoning models (GPT-5) */
  reasoningEffort?: "minimal" | "low" | "medium" | "high" | "xhigh" | undefined;

  /** Enable parallel tool calls */
  parallelToolCalls?: boolean | undefined;

  /** User identifier for abuse detection */
  user?: string | undefined;

  /** Enable logprobs (boolean or number of top logprobs) */
  logprobs?: boolean | number | undefined;

  /** Modify likelihood of specific tokens */
  logitBias?: Record<number, number> | undefined;

  /** Enable structured outputs */
  structuredOutputs?: boolean | undefined;

  /** Strict JSON schema validation */
  strictJsonSchema?: boolean | undefined;

  /** Max completion tokens (overrides maxOutputTokens for reasoning models) */
  maxCompletionTokens?: number | undefined;

  /** Enable storage of conversation */
  store?: boolean | undefined;

  /** Metadata for stored conversations */
  metadata?: Record<string, string> | undefined;

  /** Prediction mode parameters */
  prediction?: Record<string, unknown> | undefined;

  /** Service tier selection */
  serviceTier?: "auto" | "flex" | "priority" | "default" | undefined;

  /** Text verbosity level */
  textVerbosity?: "low" | "medium" | "high" | undefined;

  /** Prompt cache key */
  promptCacheKey?: string | undefined;

  /** Prompt cache retention policy */
  promptCacheRetention?: "in_memory" | "24h" | undefined;

  /** Safety identifier for policy-violating users */
  safetyIdentifier?: string | undefined;
}

/**
 * Anthropic thinking configuration
 */
export interface AnthropicThinkingConfig {
  /** Enable or disable extended thinking */
  type: "enabled" | "disabled";

  /** Token budget for thinking (min 1024 for extended thinking) */
  budgetTokens?: number | undefined;
}

/**
 * Anthropic cache control configuration
 */
export interface AnthropicCacheControl {
  /** Cache type */
  type: "ephemeral";

  /** Cache TTL (e.g., "1h") */
  ttl?: string | undefined;
}

/**
 * Anthropic-specific provider options
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/anthropic
 */
export interface AnthropicProviderOptions {
  /** Extended thinking configuration */
  thinking?: AnthropicThinkingConfig | undefined;

  /** Cache control configuration */
  cacheControl?: AnthropicCacheControl | undefined;

  /** Disable parallel tool use */
  disableParallelToolUse?: boolean | undefined;

  /** Send reasoning in response */
  sendReasoning?: boolean | undefined;

  /** Effort level */
  effort?: "high" | "medium" | "low" | undefined;

  /** Enable tool streaming */
  toolStreaming?: boolean | undefined;

  /** Structured output mode */
  structuredOutputMode?: "outputFormat" | "jsonTool" | "auto" | undefined;
}

/**
 * Google thinking configuration (Gemini)
 */
export interface GoogleThinkingConfig {
  /** Thinking level (Gemini 3) */
  thinkingLevel?: "low" | "high" | undefined;

  /** Thinking budget in tokens (Gemini 2.5) */
  thinkingBudget?: number | undefined;

  /** Include thinking in response */
  includeThoughts?: boolean | undefined;
}

/**
 * Google safety setting
 */
export interface GoogleSafetySetting {
  /** Safety category (HARM_CATEGORY_*) */
  category: string;

  /** Block threshold (BLOCK_*) */
  threshold: string;
}

/**
 * Google-specific provider options
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/google-generative-ai
 */
export interface GoogleProviderOptions {
  /** Thinking configuration */
  thinkingConfig?: GoogleThinkingConfig | undefined;

  /** Cached content identifier */
  cachedContent?: string | undefined;

  /** Enable structured outputs */
  structuredOutputs?: boolean | undefined;

  /** Safety settings array */
  safetySettings?: GoogleSafetySetting[] | undefined;

  /** Response modalities */
  responseModalities?: string[] | undefined;
}

/**
 * DeepSeek thinking configuration
 */
export interface DeepSeekThinkingConfig {
  /** Enable or disable deepseek-reasoner mode */
  type: "enabled" | "disabled";
}

/**
 * DeepSeek-specific provider options
 *
 * @see https://api-docs.deepseek.com/
 */
export interface DeepSeekProviderOptions {
  /** Thinking configuration (enables reasoning mode) */
  thinking?: DeepSeekThinkingConfig | undefined;
}

/**
 * Sno on-prem GPU-specific provider options
 */
export interface SnogpuProviderOptions {
  /** Enable thinking mode for Qwen3.5. Default: false. */
  enableThinking?: boolean | undefined;

  /** Token budget for thinking/reasoning. Only applies when enableThinking is true. */
  thinkingBudget?: number | undefined;

  /** GPU routing path: 'extract' (GPU 0, SMR) or 'reason' (GPU 1, EKG). Omit for legacy /v1. */
  gpuPath?: string | undefined;
}

/**
 * Union type for all provider options
 *
 * Keys match AI SDK v6 providerOptions structure.
 */
export interface ProviderOptions {
  openai?: OpenAIProviderOptions | undefined;
  anthropic?: AnthropicProviderOptions | undefined;
  google?: GoogleProviderOptions | undefined;
  deepseek?: DeepSeekProviderOptions | undefined;
  snogpu?: SnogpuProviderOptions | undefined;
}

/**
 * Full LLM configuration schema
 *
 * This schema mirrors AI SDK v6 exactly - values are passed directly
 * to generateText/streamText without translation.
 */
export interface LLMConfig {
  /** LLM provider (required) */
  provider: Provider;

  /** Provider-specific model ID (required) */
  model: string;

  /** Common AI SDK v6 parameters */
  common?: CommonParams | undefined;

  /** Provider-specific options */
  providerOptions?: ProviderOptions | undefined;

  /**
   * Timeout configuration for this preset
   *
   * Per-preset timeout allows reasoning models to have longer timeouts.
   * Fallback chain: preset.timeout → clientConfig.callTimeoutMs → 120000ms
   */
  timeout?: TimeoutConfig | undefined;

  /** Human-readable description (metadata, not passed to LLM) */
  description?: string | undefined;

  /** Mark config as deprecated (metadata) */
  deprecated?: boolean | undefined;

  /** Tags for organization/filtering (metadata) */
  tags?: string[] | undefined;

  /**
   * Caching strategy for this preset
   *
   * Controls how LLM responses and prompts are cached:
   * - "native": Use provider's native caching (OpenAI/Anthropic prompt caching)
   *             Routes through Helicone for OpenAI, provides 90% cost savings
   *             Key can be provided via caching.key OR CallOptions.promptCacheKey (from Promptix)
   * - "gateway": Use AI Gateway response caching (CF AI Gateway)
   *              Exact match only, good for identical requests
   * - "disabled": No caching, always fresh calls
   *
   * @default "gateway"
   */
  caching?: CachingConfig | undefined;

  /**
   * @deprecated Use caching.strategy instead
   * Legacy flag: true maps to caching.strategy="native"
   */
  bypassGateway?: boolean | undefined;
}

/**
 * Resolved LLM configuration
 *
 * Represents a fully parsed and validated config ready for use.
 */
export interface ResolvedLLMConfig extends LLMConfig {
  /** ConfigId that was resolved (canonical format) */
  configId: string;

  /** The scope used in resolution */
  scope: string;

  /** The module used in resolution */
  module: string;

  /** The preset used in resolution */
  preset: string;

  /** The version used in resolution */
  version: number;
}

// =============================================================================
// LOADING OPTIONS
// =============================================================================

/**
 * Options for loading a config from the cascade
 *
 * Used internally by LLMConfigLoader.
 */
export interface LoadConfigOptions {
  /** Deployment scope (default: "default") */
  scope?: string | undefined;

  /** Functional module (e.g., "hrkg", "memobase") */
  module: string;

  /** User ID for user-specific overrides ("_" for global) */
  userId?: string | undefined;

  /** Config preset name (e.g., "extraction", "search") */
  preset: string;

  /** Config version (default: 1) */
  version?: number | undefined;

  /**
   * Bypass LRU cache and load fresh from file
   *
   * Used by A/B experiment switching to ensure fresh config when experiment is active.
   * @default false
   */
  forceRefresh?: boolean | undefined;
}

// =============================================================================
// A/B EXPERIMENT TYPES
// =============================================================================

/**
 * A/B Experiment configuration stored in Redis
 *
 * Redis key: `experiment:llm:{module}:{preset}`
 * Example: `experiment:llm:hrkg:extraction`
 *
 * Simple 100% toggle: enabled=true -> all traffic to experiment version
 */
export interface ExperimentConfig {
  /** Whether the experiment is currently active */
  enabled: boolean;

  /** Version to use when experiment is enabled (e.g., 2 for v2) */
  version: number;

  /** ISO timestamp when experiment was enabled */
  enabledAt: string;
}

/**
 * Telemetry context for LLM calls
 *
 * Pass to LLMClient.call() for attribution tracking.
 */
export interface TelemetryContext {
  /** User identifier */
  userId?: string | undefined;

  /** Workspace identifier */
  workspaceId?: string | undefined;

  /** Project identifier */
  projectId?: string | undefined;

  /** Feature name for tracking */
  featureName?: string | undefined;

  /** Trace ID for correlation across services */
  traceId?: string | undefined;

  /** Session ID for grouping related LLM calls */
  sessionId?: string | undefined;

  /** Conversation/thread ID for multi-turn conversations */
  conversationId?: string | undefined;

  /** Turn number in conversation (1, 2, 3...) */
  turnNumber?: number | undefined;

  /** Experiment ID for A/B testing */
  experimentId?: string | undefined;

  /** Variant ID within experiment (control/treatment) */
  variantId?: string | undefined;

  /** Prompt template version for iteration tracking */
  promptVersion?: string | undefined;

  /** Whether a fallback was used (e.g., different model, default value) */
  fallbackUsed?: boolean | undefined;

  /** Reason for fallback if used */
  fallbackReason?: string | undefined;
}

// =============================================================================
// TELEMETRY PROVIDER INTERFACE (for dependency injection)
// =============================================================================

/**
 * LLM call event data for telemetry tracking
 *
 * Implementations should map this to their specific telemetry format.
 */
export interface LLMCallEventData {
  /** Config ID that was resolved */
  configId: string;

  /** Provider (openai, anthropic, google, deepseek) */
  provider: Provider;

  /** Model used */
  model: string;

  /** Module from config resolution */
  module: string;

  /** Preset from config resolution */
  preset: string;

  /** Scope from config resolution */
  scope: string;

  /** Config version */
  version: number;

  /** Input tokens consumed */
  inputTokens: number;

  /** Output tokens generated */
  outputTokens: number;

  /** Total tokens */
  totalTokens: number;

  /** Latency in milliseconds */
  latencyMs: number;

  /** Whether the call succeeded */
  success: boolean;

  /** Error message if failed */
  errorMessage?: string | undefined;

  /** Telemetry context passed by caller */
  context?: TelemetryContext | undefined;

  /** Input messages (for tracing systems that capture payloads) */
  messages?: unknown[] | undefined;

  /** Output text (for tracing systems that capture payloads) */
  output?: string | undefined;
}

/**
 * Telemetry provider interface for dependency injection
 *
 * Implement this interface to integrate LLMix with your telemetry system.
 * LLMix calls this on every LLM call (success or failure).
 *
 * @example
 * ```typescript
 * const telemetryProvider: LLMixTelemetryProvider = {
 *   async trackLLMCall(event) {
 *     // Send to your telemetry system (PostHog or equivalent)
 *     await posthog.capture('llm_call', event);
 *   },
 *   calculateCost(model, inputTokens, outputTokens) {
 *     // Return cost breakdown or null to skip cost tracking
 *     return { inputCostUsd: 0.001, outputCostUsd: 0.002, totalCostUsd: 0.003 };
 *   }
 * };
 *
 * const client = createLLMClient({ loader, telemetry: telemetryProvider });
 * ```
 */
export interface LLMixTelemetryProvider {
  /**
   * Track an LLM call event
   *
   * Called after every LLM call (success or failure).
   * Implementation should be best-effort (don't throw).
   */
  trackLLMCall(event: LLMCallEventData): Promise<void>;

  /**
   * Calculate cost for a model call (optional)
   *
   * Return cost breakdown or null to skip cost tracking.
   * Called before trackLLMCall to enrich the event.
   */
  calculateCost?(
    model: string,
    inputTokens: number,
    outputTokens: number
  ): { inputCostUsd: number; outputCostUsd: number; totalCostUsd: number } | null;
}

/**
 * Runtime overrides for LLM calls
 *
 * Merged with config values at call time.
 */
export interface RuntimeOverrides {
  /** Override model (transitional support) */
  model?: string | undefined;

  /** Override common parameters */
  common?: Partial<CommonParams> | undefined;

  /** Override provider options */
  providerOptions?: Partial<ProviderOptions> | undefined;

  /** Bypass AI Gateway for native provider features (e.g., OpenAI prompt caching) */
  bypassGateway?: boolean | undefined;
}

/**
 * Options for LLMClient.call()
 *
 * @example
 * ```typescript
 * const response = await client.call({
 *   preset: 'hrkg:extraction',
 *   messages: modelMessages,
 *   userId: 'user123',
 *   overrides: { common: { temperature: 0.5 } },
 * });
 * ```
 */
export interface CallOptions {
  /**
   * Preset string in format "module:preset" or just "preset"
   *
   * - "hrkg:extraction" -> module=hrkg, preset=extraction
   * - "extraction" -> module=_default, preset=extraction
   */
  preset: string;

  /** Messages to send to the LLM */
  messages: unknown[];

  /** Deployment scope (default: defaultScope from config) */
  scope?: string | undefined;

  /** User ID for per-user config overrides */
  userId?: string | undefined;

  /** Config version (default: 1) */
  version?: number | undefined;

  /** Runtime overrides (merged with config) */
  overrides?: RuntimeOverrides | undefined;

  /** Telemetry context */
  telemetry?: TelemetryContext | undefined;

  /**
   * Cache key for native prompt caching (OpenAI/Anthropic).
   * Usually obtained from Promptix: prompt.promptCacheKey
   * Format: "{category}:{promptName}:v{version}"
   */
  promptCacheKey?: string | undefined;
}

// =============================================================================
// RESPONSE TYPES
// =============================================================================

/**
 * Token usage statistics from LLM call
 */
export interface LLMUsage {
  /** Input/prompt tokens consumed */
  inputTokens: number;

  /** Output/completion tokens generated */
  outputTokens: number;

  /** Total tokens (input + output) */
  totalTokens: number;

  /** Cached input tokens (provider-dependent, may be undefined) */
  cachedInputTokens?: number | undefined;
}

/**
 * Response from LLMClient.call()
 */
export interface LLMResponse {
  /** Generated content */
  content: string;

  /** Model used for generation */
  model: string;

  /**
   * Provider used for generation
   * LH: "unknown" when config load fails (before provider is resolved)
   */
  provider: ProviderOrUnknown;

  /** Token usage statistics */
  usage: LLMUsage;

  /**
   * The resolved config that was used
   * LH: undefined when config load fails (before config is resolved)
   */
  config?: ResolvedLLMConfig | undefined;

  /** Whether the call succeeded */
  success: boolean;

  /** Error message if success is false */
  error?: string | undefined;

  /** Captured thinking content stripped from response (when keepThinkingOutput is false) */
  thinkingContent?: string | undefined;

  /** Indicates which cache tier served this response, if any. */
  cacheHit?: CacheHitTier | undefined;
}

/**
 * Config capabilities for runtime decisions
 *
 * Replaces env-based model detection (isProprietaryModel, getModelForTask).
 */
export interface ConfigCapabilities {
  /** The provider (openai, anthropic, google, deepseek) */
  provider: Provider;

  /** Whether the provider is proprietary (not open-source) */
  isProprietary: boolean;

  /**
   * Whether the model supports OpenAI Batch API
   *
   * True IFF: provider === 'openai' AND model is in BATCH_CAPABLE_MODELS
   * HRKG uses this for topic-analysis batching.
   */
  supportsOpenAIBatch: boolean;
}

/**
 * Result from getResolvedConfig()
 */
export interface ResolvedConfigResult {
  /** The resolved configuration */
  config: ResolvedLLMConfig;

  /** Capabilities derived from the config */
  capabilities: ConfigCapabilities;
}

// =============================================================================
// CACHE STATISTICS
// =============================================================================

/**
 * Statistics for LRU cache
 */
export interface LRUCacheStats {
  /** Current number of items in cache */
  size: number;

  /** Maximum cache size */
  maxSize: number;

  /** Cache hit count */
  hits: number;

  /** Cache miss count */
  misses: number;

  /** Hit rate percentage (0-100) */
  hitRate: number;
}

/**
 * Combined cache statistics for LLMConfigLoader
 */
export interface CacheStats {
  /** LRU cache statistics */
  localCache: LRUCacheStats;

  /** Whether Redis is currently available */
  redisAvailable: boolean;
}

// =============================================================================
// ERRORS
// =============================================================================

/**
 * Base error for LLM config operations
 */
export class LLMConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LLMConfigError";
    Object.setPrototypeOf(this, LLMConfigError.prototype);
  }
}

/**
 * Thrown when a config cannot be found in the cascade
 */
export class ConfigNotFoundError extends LLMConfigError {
  constructor(message: string) {
    super(message);
    this.name = "ConfigNotFoundError";
    Object.setPrototypeOf(this, ConfigNotFoundError.prototype);
  }
}

/**
 * Thrown when a config file exists but cannot be read (e.g., EACCES)
 */
export class ConfigAccessError extends LLMConfigError {
  constructor(message: string) {
    super(message);
    this.name = "ConfigAccessError";
    Object.setPrototypeOf(this, ConfigAccessError.prototype);
  }
}

/**
 * Thrown when a config file is invalid (schema validation failed)
 */
export class InvalidConfigError extends LLMConfigError {
  constructor(message: string) {
    super(message);
    this.name = "InvalidConfigError";
    Object.setPrototypeOf(this, InvalidConfigError.prototype);
  }
}

/**
 * Thrown when a security violation is detected (e.g., path traversal)
 */
export class SecurityError extends LLMConfigError {
  constructor(message: string) {
    super(message);
    this.name = "SecurityError";
    Object.setPrototypeOf(this, SecurityError.prototype);
  }
}

// =============================================================================
// VALIDATION CONSTANTS
// =============================================================================

/**
 * Pattern for valid module names
 *
 * Allows: _default, or lowercase alphanumeric with underscores starting with letter
 * Examples: hrkg, memobase, _default, memu_v2
 */
export const VALID_MODULE_PATTERN = /^(_default|[a-z][a-z0-9_]{0,63})$/;

/**
 * Pattern for valid preset names
 *
 * Allows: _base (and _base_*), or lowercase alphanumeric with underscores starting with letter
 * Examples: extraction, search, _base, _base_low
 */
export const VALID_PRESET_PATTERN = /^(_base[a-z0-9_]*|[a-z][a-z0-9_]{0,63})$/;

/**
 * Pattern for valid scope names
 *
 * Allows: _default, or lowercase alphanumeric with underscores/hyphens starting with letter
 * Examples: default, staging, production, _default
 */
export const VALID_SCOPE_PATTERN = /^(_default|[a-z][a-z0-9_-]{0,63})$/;

/**
 * Pattern for valid user IDs
 *
 * Allows: alphanumeric with underscores and hyphens, 1-64 characters
 * "_" is reserved for global (no user-specific) config
 * Examples: user123, _, user-abc, abc_123
 */
export const VALID_USER_ID_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;

/** Minimum allowed version number */
export const MIN_VERSION = 1;

/** Maximum allowed version number */
export const MAX_VERSION = 9999;

/** Valid providers list */
export const VALID_PROVIDERS: readonly Provider[] = [
  "openai",
  "anthropic",
  "google",
  "deepseek",
] as const;

/** Minimum budgetTokens for Anthropic extended thinking */
export const ANTHROPIC_MIN_BUDGET_TOKENS = 1024;

/**
 * OpenAI prompt cache minimum token threshold.
 * Prompts must be >= this many tokens for OpenAI's automatic prompt caching to activate.
 * @see https://platform.openai.com/docs/guides/prompt-caching
 */
export const OPENAI_PROMPT_CACHE_MIN_TOKENS = 1024;
