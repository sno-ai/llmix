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
 * preset runtime path. TypeScript presets are authored as MDA Source Mode
 * files and projected into the runtime LLMConfig shape.
 */

// =============================================================================
// PUBLIC API
// =============================================================================

export {
  buildMdaConfigFilePath,
  loadMdaConfig,
  loadMdaConfigFromFile,
  loadMdaConfigPreset,
  resolveConfigDir,
  type LLMixPathConfig,
  type MdaConfigLoadOptions,
  type ResolvedConfigDir,
} from "./config.js";
export {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  type ConfigRegistryJsonObject,
  type ConfigRegistryJsonValue,
  type ConfigRegistryOpenOptions,
  type ConfigRegistryPublishOptions,
  type PublishedRevision,
  type RegistryRootCurrentBinding,
  type RegistryRootEnvelope,
  type RegistryRootFileDigest,
  type RegistryRootFreshnessInput,
  type RegistryRootHighWatermark,
  type RegistryRootIntegrity,
  type RegistryRootManifestBinding,
  type RegistryRootPayload,
  type RegistryRootSignature,
  type RegistryRootSigner,
  type RegistryRootSigningInput,
  type RegistryRootSigningOptions,
  type RegistryRootVerificationOptions,
} from "./config-registry.js";

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
} from "./pipeline.js";

export {
  anthropicDispatch,
  deepinfraDispatch,
  geminiDispatch,
  novitaDispatch,
  openaiDispatch,
  openrouterDispatch,
  snoGpuDispatch,
  togetherDispatch,
} from "./dispatchers.js";

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
} from "./types.js";

// =============================================================================
// ERRORS
// =============================================================================

export {
  ConfigAccessError,
  ConfigNotFoundError,
  InvalidConfigError,
  LLMConfigError,
  SecurityError,
} from "./types.js";

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
} from "./types.js";

// =============================================================================
// MODEL CAPABILITIES (for filtering unsupported params)
// =============================================================================

export {
  adjustTemperatureForModel,
  filterOpenAIProviderOptions,
  getModelCapabilities,
  type FilteredParams,
  type ModelCapabilities,
} from "./model-capabilities.js";

// =============================================================================
// FEATURE MODULES
// =============================================================================

export {
  AdaptiveSemaphore,
  parseOpenAIRatelimitHeaders,
} from "./adaptive-semaphore.js";

export {
  KeyPool,
  KeyPoolExhaustedError,
  loadKeysFromEnv,
} from "./key-pool.js";

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
} from "./resilience.js";

export {
  stripThinking,
  type StripThinkingResult,
} from "./thinking.js";

export {
  applyTransformKwargs,
  geminiTransformKwargs,
  openaiTransformKwargs,
  openrouterTransformKwargs,
  snoGpuTransformKwargs,
  PROVIDER_KWARGS_REGISTRY,
  type TransformKwargsCallback,
  type TransformKwargsContext,
} from "./provider-kwargs.js";

export {
  getProviderTransport,
  createOpenAITransport,
  PROVIDER_TRANSPORT,
  type ProviderTransportConfig,
} from "./http2.js";

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
} from "./batch.js";

// =============================================================================
// INTERNAL UTILITIES (for advanced use cases)
// =============================================================================

export { LRUCache } from "./lru-cache.js";

export {
  TwoTierCache,
  generateCacheKey,
  resolveResponseCacheStrategy,
  isResponseCacheStrategy,
  type TwoTierCacheConfig,
  type ResponseCacheStats,
} from "./response-cache.js";

export {
  AnthropicProviderOptionsSchema,
  CachingConfigSchema,
  // Zod schemas for external validation
  CommonParamsSchema,
  DeepSeekProviderOptionsSchema,
  GoogleProviderOptionsSchema,
  LLMConfigSchema,
  LLMixMdaPresetSchema,
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
  type LLMixMdaPreset,
} from "./mda-loader.js";
