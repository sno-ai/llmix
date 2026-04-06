/**
 * LLMix - LLM Config Loader Package
 *
 * Three-tier caching system for LLM configurations with AI SDK v6 alignment.
 *
 * Architecture:
 * 1. Local LRU cache (0.1ms)
 * 2. Shared Redis (1-2ms)
 * 3. File system with cascade resolution (5-10ms)
 *
 * @example
 * ```typescript
 * import { createLLMConfigLoader, createLLMClient } from '@sno-cortex/llmix';
 *
 * // Create and initialize loader
 * const loader = createLLMConfigLoader({
 *   configDir: '/app/config/llm',
 *   redisUrl: process.env.REDIS_KV_URL,
 * });
 * await loader.init();
 *
 * // Create client
 * const client = createLLMClient({ loader });
 *
 * // Make LLM call
 * const response = await client.call({
 *   preset: 'hrkg:extraction',
 *   messages: [{ role: 'user', content: 'Extract entities from: ...' }],
 * });
 *
 * // Get config + capabilities without making a call
 * const { config, capabilities } = await client.getResolvedConfig({
 *   preset: 'hrkg:topic-analysis',
 * });
 *
 * if (capabilities.supportsOpenAIBatch) {
 *   // Use batch API for efficiency
 * }
 * ```
 */

// =============================================================================
// MAIN CLASSES & FACTORIES
// =============================================================================

export {
  createLLMClient,
  LLMClient,
  type ApiKeysConfig,
  type HeliconeConfig,
  type LLMClientConfig,
  type ProviderUrlConfig,
} from "./client";
export { resolveConfigDir, type LLMixPathConfig, type ResolvedConfigDir } from "./config";
export { createLLMConfigLoader, LLMConfigLoader } from "./config-loader";

// =============================================================================
// V2 CALL PIPELINE
// =============================================================================

export {
  V2CallPipeline,
  type DispatchContext,
  type ProviderDispatchFn,
  type ProviderError,
  type ProviderResult,
  type V2CallInput,
  type V2CallResponse,
  type V2PipelineConfig,
} from "./client-v2";

// =============================================================================
// TYPES
// =============================================================================

// Configuration types
// LLM config schema types
// Response types
// Cache types
// Telemetry types (for dependency injection)
export type {
  AnthropicCacheControl,
  AnthropicProviderOptions,
  AnthropicThinkingConfig,
  CacheHitTier,
  CacheStats,
  CachingConfig,
  CachingStrategy,
  CallOptions,
  CommonParams,
  ConfigCapabilities,
  DeepSeekProviderOptions,
  DeepSeekThinkingConfig,
  ExperimentConfig,
  GoogleProviderOptions,
  GoogleSafetySetting,
  GoogleThinkingConfig,
  LLMCallEventData,
  LLMConfig,
  LLMConfigLoaderConfig,
  LLMConfigLoaderLogger,
  LLMixTelemetryProvider,
  LLMResponse,
  LLMUsage,
  LoadConfigOptions,
  LRUCacheStats,
  OpenAIProviderOptions,
  Provider,
  ProviderOptions,
  ProviderOrUnknown,
  ResponseCacheStrategy,
  ResolvedConfigResult,
  ResolvedLLMConfig,
  RuntimeOverrides,
  SnogpuProviderOptions,
  TelemetryContext,
  TimeoutConfig,
} from "./types";

// =============================================================================
// ERRORS
// =============================================================================

export {
  ConfigNotFoundError,
  InvalidConfigError,
  LLMConfigError,
  SecurityError,
} from "./types";

// =============================================================================
// VALIDATION CONSTANTS
// =============================================================================

export {
  ANTHROPIC_MIN_BUDGET_TOKENS,
  MAX_VERSION,
  MIN_VERSION,
  VALID_MODULE_PATTERN,
  VALID_PRESET_PATTERN,
  VALID_PROVIDERS,
  VALID_SCOPE_PATTERN,
  VALID_USER_ID_PATTERN,
} from "./types";

// =============================================================================
// MODEL CAPABILITIES (for filtering unsupported params)
// =============================================================================

export {
  adjustTemperatureForModel,
  filterOpenAIProviderOptions,
  getModelCapabilities,
  type FilteredParams,
  type ModelCapabilities,
} from "./model-capabilities";

// =============================================================================
// V2 FEATURE MODULES
// =============================================================================

export {
  AdaptiveSemaphore,
  parseOpenAIRatelimitHeaders,
} from "./adaptive-semaphore";

export {
  KeyPool,
  KeyPoolExhaustedError,
  loadKeysFromEnv,
} from "./key-pool";

export {
  CircuitBreaker,
  CircuitOpenError,
  CircuitState,
  KillSwitch,
  KillSwitchActiveError,
  RetryPolicy,
  Singleflight,
  calculateDelay,
  createFileLock,
  isRetryable,
  parseRetryAfter,
  type CircuitBreakerOptions,
  type FileLockLike,
  type RetryPolicyOptions,
} from "./resilience";

export {
  stripThinking,
  type StripThinkingResult,
} from "./thinking";

export {
  applyTransformKwargs,
  geminiTransformKwargs,
  openaiTransformKwargs,
  openrouterTransformKwargs,
  snogpuTransformKwargs,
  PROVIDER_KWARGS_REGISTRY,
  type TransformKwargsCallback,
  type TransformKwargsContext,
} from "./provider-kwargs";

export {
  getProviderTransport,
  createOpenAITransport,
  PROVIDER_TRANSPORT,
  type ProviderTransportConfig,
} from "./http2";

export {
  BatchProcessor,
  encodeBatchId,
  decodeBatchId,
  writeMetadata as writeBatchMetadata,
  readMetadata as readBatchMetadata,
  deleteMetadata as deleteBatchMetadata,
  type BatchProvider,
  type BatchState,
  type BatchStatus,
  type BatchResult,
  type BatchMetadata,
  type BatchSubmitOptions,
  type DecodedBatchId,
} from "./batch";

// =============================================================================
// INTERNAL UTILITIES (for advanced use cases)
// =============================================================================

export { LRUCache } from "./lru-cache";

export {
  TwoTierCache,
  generateCacheKey,
  resolveResponseCacheStrategy,
  isResponseCacheStrategy,
  type TwoTierCacheConfig,
  type ResponseCacheStats,
} from "./response-cache";

export {
  AnthropicProviderOptionsSchema,
  buildConfigFilePath,
  CachingConfigSchema,
  // Zod schemas for external validation
  CommonParamsSchema,
  DeepSeekProviderOptionsSchema,
  GoogleProviderOptionsSchema,
  LLMConfigSchema,
  loadConfigFromFile,
  OpenAIProviderOptionsSchema,
  ProviderOptionsSchema,
  SnogpuProviderOptionsSchema,
  validateModule,
  validatePreset,
  validateScope,
  validateUserId,
  validateVersion,
  verifyPathContainment,
  verifyPathContainmentAsync,
} from "./yaml-loader";
