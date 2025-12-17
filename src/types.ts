/**
 * LLMix Types
 *
 * Type definitions for the LLM Config Loader package.
 * Schema mirrors AI SDK v5 exactly - no parameter renaming required.
 *
 * @see https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text
 */

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
  redisUrl?: string;

  /** LRU cache max size (default: 100) */
  cacheSize?: number;

  /** Local cache TTL in seconds (default: 21600 = 6 hours) */
  cacheTtlSeconds?: number;

  /** Redis cache TTL in seconds (default: 86400 = 24 hours) */
  redisTtlSeconds?: number;

  /** Redis connection timeout in ms (default: 5000) */
  redisConnectTimeoutMs?: number;

  /** Redis command timeout in ms (default: 5000) */
  redisCommandTimeoutMs?: number;

  /** Max retries per Redis request (default: 3) */
  redisMaxRetries?: number;

  /** Default scope for config resolution (default: "default") */
  defaultScope?: string;

  /** Custom logger - uses console if not provided */
  logger?: LLMConfigLoaderLogger;
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
// LLM CONFIG SCHEMA - AI SDK V5 ALIGNED
// =============================================================================

/**
 * Supported LLM providers
 */
export type Provider = "openai" | "anthropic" | "google" | "deepseek";

/** Provider type with unknown for error cases (config load failures) */
export type ProviderOrUnknown = Provider | "unknown";

/**
 * Common AI SDK v5 parameters
 *
 * These map directly to generateText/streamText params.
 * @see https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text
 */
export interface CommonParams {
  /** Max tokens to generate */
  maxOutputTokens?: number;

  /** Temperature 0.0-2.0 (don't use with topP) */
  temperature?: number;

  /** Top-p sampling 0.0-1.0 (don't use with temperature) */
  topP?: number;

  /** Sample from top K options */
  topK?: number;

  /** Reduce repetition of existing info */
  presencePenalty?: number;

  /** Reduce reuse of identical phrases */
  frequencyPenalty?: number;

  /** Sequences that halt generation */
  stopSequences?: string[];

  /** Seed for deterministic results */
  seed?: number;

  /** Retry attempts (default: 2) */
  maxRetries?: number;
}

// =============================================================================
// PROVIDER-SPECIFIC OPTIONS - AI SDK V5 ALIGNED
// =============================================================================

/**
 * OpenAI-specific provider options
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/openai
 */
export interface OpenAIProviderOptions {
  /** Reasoning effort for reasoning models (GPT-5) */
  reasoningEffort?: "minimal" | "low" | "medium" | "high" | "xhigh";

  /** Enable parallel tool calls */
  parallelToolCalls?: boolean;

  /** User identifier for abuse detection */
  user?: string;

  /** Enable logprobs (boolean or number of top logprobs) */
  logprobs?: boolean | number;

  /** Modify likelihood of specific tokens */
  logitBias?: Record<number, number>;

  /** Enable structured outputs */
  structuredOutputs?: boolean;

  /** Strict JSON schema validation */
  strictJsonSchema?: boolean;

  /** Max completion tokens (overrides maxOutputTokens for reasoning models) */
  maxCompletionTokens?: number;

  /** Enable storage of conversation */
  store?: boolean;

  /** Metadata for stored conversations */
  metadata?: Record<string, string>;

  /** Prediction mode parameters */
  prediction?: Record<string, unknown>;

  /** Service tier selection */
  serviceTier?: "auto" | "flex" | "priority" | "default";

  /** Text verbosity level */
  textVerbosity?: "low" | "medium" | "high";

  /** Prompt cache key */
  promptCacheKey?: string;

  /** Prompt cache retention policy */
  promptCacheRetention?: "in_memory" | "24h";

  /** Safety identifier for policy-violating users */
  safetyIdentifier?: string;
}

/**
 * Anthropic thinking configuration
 */
export interface AnthropicThinkingConfig {
  /** Enable or disable extended thinking */
  type: "enabled" | "disabled";

  /** Token budget for thinking (min 1024 for extended thinking) */
  budgetTokens?: number;
}

/**
 * Anthropic cache control configuration
 */
export interface AnthropicCacheControl {
  /** Cache type */
  type: "ephemeral";

  /** Cache TTL (e.g., "1h") */
  ttl?: string;
}

/**
 * Anthropic-specific provider options
 *
 * @see https://ai-sdk.dev/providers/ai-sdk-providers/anthropic
 */
export interface AnthropicProviderOptions {
  /** Extended thinking configuration */
  thinking?: AnthropicThinkingConfig;

  /** Cache control configuration */
  cacheControl?: AnthropicCacheControl;

  /** Disable parallel tool use */
  disableParallelToolUse?: boolean;

  /** Send reasoning in response */
  sendReasoning?: boolean;

  /** Effort level */
  effort?: "high" | "medium" | "low";

  /** Enable tool streaming */
  toolStreaming?: boolean;

  /** Structured output mode */
  structuredOutputMode?: "outputFormat" | "jsonTool" | "auto";
}

/**
 * Google thinking configuration (Gemini)
 */
export interface GoogleThinkingConfig {
  /** Thinking level (Gemini 3) */
  thinkingLevel?: "low" | "high";

  /** Thinking budget in tokens (Gemini 2.5) */
  thinkingBudget?: number;

  /** Include thinking in response */
  includeThoughts?: boolean;
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
  thinkingConfig?: GoogleThinkingConfig;

  /** Cached content identifier */
  cachedContent?: string;

  /** Enable structured outputs */
  structuredOutputs?: boolean;

  /** Safety settings array */
  safetySettings?: GoogleSafetySetting[];

  /** Response modalities */
  responseModalities?: string[];
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
  thinking?: DeepSeekThinkingConfig;
}

/**
 * Union type for all provider options
 *
 * Keys match AI SDK v5 providerOptions structure.
 */
export interface ProviderOptions {
  openai?: OpenAIProviderOptions;
  anthropic?: AnthropicProviderOptions;
  google?: GoogleProviderOptions;
  deepseek?: DeepSeekProviderOptions;
}

/**
 * Full LLM configuration schema
 *
 * This schema mirrors AI SDK v5 exactly - values are passed directly
 * to generateText/streamText without translation.
 */
export interface LLMConfig {
  /** LLM provider (required) */
  provider: Provider;

  /** Provider-specific model ID (required) */
  model: string;

  /** Common AI SDK v5 parameters */
  common?: CommonParams;

  /** Provider-specific options */
  providerOptions?: ProviderOptions;

  /** Human-readable description (metadata, not passed to LLM) */
  description?: string;

  /** Mark config as deprecated (metadata) */
  deprecated?: boolean;

  /** Tags for organization/filtering (metadata) */
  tags?: string[];

  /**
   * Bypass AI Gateway (e.g., Cloudflare) and use direct provider URLs
   *
   * When true, ignores configured gateway URLs and calls providers directly.
   * Use this for profiles that need provider-native features like:
   * - OpenAI prompt caching (promptCacheKey/promptCacheRetention)
   * - Provider-specific options not supported by gateways
   *
   * @default false
   */
  bypassGateway?: boolean;
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

  /** The profile used in resolution */
  profile: string;

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
  scope?: string;

  /** Functional module (e.g., "hrkg", "memobase") */
  module: string;

  /** User ID for user-specific overrides ("_" for global) */
  userId?: string;

  /** Config profile name (e.g., "extraction", "search") */
  profile: string;

  /** Config version (default: 1) */
  version?: number;
}

/**
 * Telemetry context for LLM calls
 *
 * Pass to LLMClient.call() for attribution tracking.
 */
export interface TelemetryContext {
  /** User identifier */
  userId?: string;

  /** Workspace identifier */
  workspaceId?: string;

  /** Project identifier */
  projectId?: string;

  /** Feature name for tracking */
  featureName?: string;

  /** Trace ID for correlation across services */
  traceId?: string;

  /** Session ID for grouping related LLM calls */
  sessionId?: string;

  /** Conversation/thread ID for multi-turn conversations */
  conversationId?: string;

  /** Turn number in conversation (1, 2, 3...) */
  turnNumber?: number;

  /** Experiment ID for A/B testing */
  experimentId?: string;

  /** Variant ID within experiment (control/treatment) */
  variantId?: string;

  /** Prompt template version for iteration tracking */
  promptVersion?: string;

  /** Whether a fallback was used (e.g., different model, default value) */
  fallbackUsed?: boolean;

  /** Reason for fallback if used */
  fallbackReason?: string;
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

  /** Profile from config resolution */
  profile: string;

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
  errorMessage?: string;

  /** Telemetry context passed by caller */
  context?: TelemetryContext;

  /** Input messages (for tracing systems that capture payloads) */
  messages?: unknown[];

  /** Output text (for tracing systems that capture payloads) */
  output?: string;
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
 *     // Send to your telemetry system (PostHog, Langfuse, etc.)
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
  model?: string;

  /** Override common parameters */
  common?: Partial<CommonParams>;

  /** Override provider options */
  providerOptions?: Partial<ProviderOptions>;

  /** Bypass AI Gateway for native provider features (e.g., OpenAI prompt caching) */
  bypassGateway?: boolean;
}

/**
 * Options for LLMClient.call()
 *
 * @example
 * ```typescript
 * const response = await client.call({
 *   profile: 'hrkg:extraction',
 *   messages: modelMessages,
 *   userId: 'user123',
 *   overrides: { common: { temperature: 0.5 } },
 * });
 * ```
 */
export interface CallOptions {
  /**
   * Profile string in format "module:profile" or just "profile"
   *
   * - "hrkg:extraction" -> module=hrkg, profile=extraction
   * - "extraction" -> module=_default, profile=extraction
   */
  profile: string;

  /** Messages to send to the LLM */
  messages: unknown[];

  /** Deployment scope (default: defaultScope from config) */
  scope?: string;

  /** User ID for per-user config overrides */
  userId?: string;

  /** Config version (default: 1) */
  version?: number;

  /** Runtime overrides (merged with config) */
  overrides?: RuntimeOverrides;

  /** Telemetry context */
  telemetry?: TelemetryContext;
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
  cachedInputTokens?: number;
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
  config?: ResolvedLLMConfig;

  /** Whether the call succeeded */
  success: boolean;

  /** Error message if success is false */
  error?: string;
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
 * Pattern for valid profile names
 *
 * Allows: _base (and _base_*), or lowercase alphanumeric with underscores starting with letter
 * Examples: extraction, search, _base, _base_low
 */
export const VALID_PROFILE_PATTERN = /^(_base[a-z0-9_]*|[a-z][a-z0-9_]{0,63})$/;

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
