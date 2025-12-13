/**
 * LLMix - LLM Config Loader Package
 *
 * Three-tier caching system for LLM configurations with AI SDK v5 alignment.
 *
 * Architecture:
 * 1. Local LRU cache (0.1ms)
 * 2. Shared Redis (1-2ms)
 * 3. File system with cascade resolution (5-10ms)
 *
 * @example
 * ```typescript
 * import { createLLMConfigLoader, createLLMClient } from '@sno-mem/llmix';
 *
 * // Create and initialize loader
 * const loader = createLLMConfigLoader({
 *   configDir: '/app/config/llm',
 *   redisUrl: process.env.REDIS_URL,
 * });
 * await loader.init();
 *
 * // Create client
 * const client = createLLMClient({ loader });
 *
 * // Make LLM call
 * const response = await client.call({
 *   profile: 'hrkg:extraction',
 *   messages: [{ role: 'user', content: 'Extract entities from: ...' }],
 * });
 *
 * // Get config + capabilities without making a call
 * const { config, capabilities } = await client.getResolvedConfig({
 *   profile: 'hrkg:topic-analysis',
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

export { createLLMClient, LLMClient, type LLMClientConfig } from "./client";
export { createLLMConfigLoader, LLMConfigLoader } from "./config-loader";

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
  CacheStats,
  CallOptions,
  CommonParams,
  ConfigCapabilities,
  DeepSeekProviderOptions,
  DeepSeekThinkingConfig,
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
  ResolvedConfigResult,
  ResolvedLLMConfig,
  RuntimeOverrides,
  TelemetryContext,
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
  VALID_PROFILE_PATTERN,
  VALID_PROVIDERS,
  VALID_SCOPE_PATTERN,
  VALID_USER_ID_PATTERN,
} from "./types";

// =============================================================================
// INTERNAL UTILITIES (for advanced use cases)
// =============================================================================

export { LRUCache } from "./lru-cache";

export {
  AnthropicProviderOptionsSchema,
  buildConfigFilePath,
  // Zod schemas for external validation
  CommonParamsSchema,
  DeepSeekProviderOptionsSchema,
  GoogleProviderOptionsSchema,
  LLMConfigSchema,
  loadConfigFromFile,
  OpenAIProviderOptionsSchema,
  ProviderOptionsSchema,
  validateModule,
  validateProfile,
  validateScope,
  validateUserId,
  validateVersion,
  verifyPathContainment,
  verifyPathContainmentAsync,
} from "./yaml-loader";
