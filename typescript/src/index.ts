/**
 * LLMix
 *
 * Cross-runtime orchestration for LLM calls in TypeScript.
 *
 * @example
 * ```typescript
 * import { CallPipeline } from "llmix";
 *
 * const pipeline = new CallPipeline({ dispatch: async (ctx) => {
 *   return {
 *     content: "ok",
 *     model: ctx.model,
 *     usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
 *   };
 * }});
 *
 * const config = {
 *   provider: "openai",
 *   model: "gpt-4.1-mini",
 * };
 *
 * const response = await pipeline.call({ config, messages: [{ role: "user", content: "hi" }] });
 * ```
 *
 * `ConfigRegistryManager` and `ConfigRegistryPublisher` are the preferred
 * preset runtime path. Direct YAML helpers remain available through
 * `loadConfig` and `loadConfigPreset`, but they are best treated as low-level
 * authoring, test, and migration helpers.
 */

// =============================================================================
// PUBLIC API
// =============================================================================

export { loadConfig, loadConfigPreset, resolveConfigDir, type LLMixPathConfig, type ResolvedConfigDir } from "./config";
export { ConfigRegistryManager, ConfigRegistryPublisher, type PublishedRevision } from "./config-registry";

// =============================================================================
// CALL PIPELINE
// =============================================================================

export {
  CallPipeline,
  type DispatchContext,
  type ProviderDispatchFn,
  type ProviderError,
  type ProviderResult,
  type CallInput,
  type CallResponse,
  type PipelineConfig,
} from "./pipeline";

export {
  anthropicDispatch,
  deepinfraDispatch,
  geminiDispatch,
  novitaDispatch,
  openaiDispatch,
  openrouterDispatch,
  snoGpuDispatch,
  togetherDispatch,
} from "./dispatchers";

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
  CommonParams,
  DeepSeekProviderOptions,
  DeepSeekThinkingConfig,
  ExperimentConfig,
  GoogleProviderOptions,
  GoogleSafetySetting,
  GoogleThinkingConfig,
  LLMCallEventData,
  LLMConfig,
  LLMixTelemetryProvider,
  LLMUsage,
  LRUCacheStats,
  OpenAIProviderOptions,
  Provider,
  ProviderOptions,
  ProviderOrUnknown,
  ResponseCacheStrategy,
  RuntimeOverrides,
  SnoGpuProviderOptions,
  TelemetryContext,
  TimeoutConfig,
} from "./types";

// =============================================================================
// ERRORS
// =============================================================================

export {
  ConfigAccessError,
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
// FEATURE MODULES
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
  snoGpuTransformKwargs,
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
  SnoGpuProviderOptionsSchema,
  validateModule,
  validatePreset,
  validateScope,
  validateUserId,
  validateVersion,
  verifyPathContainment,
  verifyPathContainmentAsync,
} from "./yaml-loader";
