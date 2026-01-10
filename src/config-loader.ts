/**
 * LLMConfigLoader - Three-Tier LLM Config Loading with Redis Caching
 *
 * Architecture:
 * 1. Local LRU cache (fastest, 0.1ms)
 * 2. Shared Redis (fast, 1-2ms)
 * 3. File system with cascade resolution (slower, 5-10ms)
 *
 * Features:
 * - Dependency injection (no hardcoded config imports)
 * - Optional Redis (works file-only if Redis unavailable)
 * - TTL-based cache invalidation (no pub/sub in v1)
 * - Graceful degradation on Redis failure
 * - 5-level cascade resolution for config inheritance
 *
 * ConfigId format: {scope}:{module}:{userId}:{profile}:v{version}
 * Redis key: llm:{scope}:{module}:{userId}:{profile}:v{version}
 *
 * @example
 * ```typescript
 * import { LLMConfigLoader } from '@sno-cortex/llmix';
 *
 * const loader = new LLMConfigLoader({
 *   configDir: '/app/config/llm',
 *   redisUrl: 'redis://localhost:6379',
 * });
 *
 * await loader.init();
 * const config = await loader.loadConfig({ module: 'hrkg', profile: 'extraction' });
 * ```
 */

import type Redis from "ioredis";
import { createLogger } from "@/utils/logger";
import { LRUCache } from "./lru-cache";

const sharedLogger = createLogger("llmix-config");
import {
  type CacheStats,
  ConfigNotFoundError,
  type ExperimentConfig,
  type LLMConfigLoaderConfig,
  type LLMConfigLoaderLogger,
  type LoadConfigOptions,
  type ResolvedLLMConfig,
} from "./types";
import {
  loadConfigFromFile,
  validateModule,
  validateProfile,
  validateScope,
  validateUserId,
  validateVersion,
} from "./yaml-loader";

// =============================================================================
// DEFAULT CONFIGURATION
// =============================================================================

const DEFAULT_CACHE_SIZE = 100;
const DEFAULT_CACHE_TTL_SECONDS = 21600; // 6 hours
const DEFAULT_REDIS_TTL_SECONDS = 86400; // 24 hours
const DEFAULT_REDIS_CONNECT_TIMEOUT_MS = 5000;
const DEFAULT_REDIS_COMMAND_TIMEOUT_MS = 5000;
const DEFAULT_REDIS_MAX_RETRIES = 3;
const DEFAULT_SCOPE = "default";

/** Max entries in warned config IDs set (rate-limited warnings) */
const MAX_WARNED_CONFIG_IDS = 1000;

/** Redis key prefix for LLM configs */
const REDIS_KEY_PREFIX = "llm:";

/** Redis key prefix for A/B experiments */
const EXPERIMENT_KEY_PREFIX = "experiment:llm:";

// =============================================================================
// DEFAULT LOGGER
// =============================================================================

const defaultLogger: LLMConfigLoaderLogger = {
  debug: (msg, ...args) => sharedLogger.debug(`${msg}`, ...(args.length ? [args[0] as Record<string, unknown>] : [])),
  info: (msg, ...args) => sharedLogger.info(`${msg}`, ...(args.length ? [args[0] as Record<string, unknown>] : [])),
  warn: (msg, ...args) => sharedLogger.warn(`${msg}`, ...(args.length ? [args[0] as Record<string, unknown>] : [])),
  error: (msg, ...args) => sharedLogger.error(`${msg}`, ...(args.length ? [args[0] as Record<string, unknown>] : [])),
};

// =============================================================================
// HELPER TYPES
// =============================================================================

/** Cascade step definition */
interface CascadeStep {
  scope: string;
  module: string;
  userId: string;
  profile: string;
  version: number;
}

// =============================================================================
// LLM CONFIG LOADER CLASS
// =============================================================================

/**
 * LLM Config Loader with three-tier caching and cascade resolution
 *
 * Loading priority:
 * 1. Local LRU cache (instant, 0.1ms)
 * 2. Redis (1-2ms)
 * 3. File system with cascade (5-10ms)
 *
 * Cascade resolution (5 levels):
 * 1. {scope}:{module}:{userId}:{profile}:v{version} - Full specific
 * 2. {scope}:{module}:_:{profile}:v{version} - Module profile (no user)
 * 3. {scope}:_default:_:{profile}:v{version} - Scope-wide fallback
 * 4. _default:_default:_:{profile}:v{version} - Global profile fallback
 * 5. _default:_default:_:_base:v1 - Ultimate base fallback
 */
export class LLMConfigLoader {
  private readonly config: Required<
    Pick<
      LLMConfigLoaderConfig,
      "configDir" | "cacheSize" | "cacheTtlSeconds" | "redisTtlSeconds" | "defaultScope"
    >
  > &
    LLMConfigLoaderConfig;

  private readonly localCache: LRUCache;
  private readonly logger: LLMConfigLoaderLogger;

  /** Rate-limited warnings: track which configIds have already warned */
  private readonly warnedConfigIds: Set<string>;

  private redisClient: Redis | null = null;
  private redisAvailable = false;
  private initialized = false;

  /**
   * Create a new LLMConfigLoader
   *
   * @param config - Loader configuration
   */
  constructor(config: LLMConfigLoaderConfig) {
    // Merge defaults
    this.config = {
      ...config,
      cacheSize: config.cacheSize ?? DEFAULT_CACHE_SIZE,
      cacheTtlSeconds: config.cacheTtlSeconds ?? DEFAULT_CACHE_TTL_SECONDS,
      redisTtlSeconds: config.redisTtlSeconds ?? DEFAULT_REDIS_TTL_SECONDS,
      defaultScope: config.defaultScope ?? DEFAULT_SCOPE,
      redisConnectTimeoutMs: config.redisConnectTimeoutMs ?? DEFAULT_REDIS_CONNECT_TIMEOUT_MS,
      redisCommandTimeoutMs: config.redisCommandTimeoutMs ?? DEFAULT_REDIS_COMMAND_TIMEOUT_MS,
      redisMaxRetries: config.redisMaxRetries ?? DEFAULT_REDIS_MAX_RETRIES,
    };

    this.logger = config.logger ?? defaultLogger;

    // Initialize local cache
    this.localCache = new LRUCache(this.config.cacheSize, this.config.cacheTtlSeconds);

    // Initialize warned config IDs set (bounded)
    this.warnedConfigIds = new Set();

    this.logger.info(
      `Created: size=${this.config.cacheSize}, ttl=${this.config.cacheTtlSeconds}s, ` +
        `redis=${config.redisUrl ? "configured" : "disabled"}`
    );
  }

  /**
   * Initialize the loader
   *
   * - Validates that _default/_base.v1.yaml exists and parses
   * - Connects to Redis (optional, graceful failure)
   *
   * @throws ConfigNotFoundError if base config is missing
   * @throws Error if base config fails to parse
   */
  async init(): Promise<void> {
    if (this.initialized) {
      this.logger.warn("Already initialized");
      return;
    }

    // Validate base config exists
    try {
      await loadConfigFromFile(this.config.configDir, "_default", "_base", 1);
      this.logger.info("Base config validated: _default/_base.v1.yaml");
    } catch (error) {
      if (error instanceof ConfigNotFoundError) {
        throw new ConfigNotFoundError(
          `LLMConfigLoader init failed: base config not found at ${this.config.configDir}/_default/_base.v1.yaml. ` +
            "This file is required as the ultimate fallback."
        );
      }
      throw error;
    }

    // Initialize Redis (optional)
    await this.initRedis();

    this.initialized = true;
    this.logger.info("Initialized successfully");
  }

  /**
   * Initialize Redis connection
   *
   * @returns True if Redis is available
   */
  private async initRedis(): Promise<boolean> {
    if (this.redisAvailable && this.redisClient) {
      return true;
    }

    if (!this.config.redisUrl) {
      this.logger.info("Redis URL not configured - using file-only mode");
      return false;
    }

    try {
      // Dynamic import to avoid requiring ioredis if not used
      const { default: RedisConstructor } = await import("ioredis");

      this.redisClient = new RedisConstructor(this.config.redisUrl, {
        maxRetriesPerRequest: this.config.redisMaxRetries,
        connectTimeout: this.config.redisConnectTimeoutMs,
        commandTimeout: this.config.redisCommandTimeoutMs,
        retryStrategy: (times: number) => {
          if (times > (this.config.redisMaxRetries ?? DEFAULT_REDIS_MAX_RETRIES)) {
            this.logger.error(`Redis max retries exceeded (${times})`);
            return null; // Stop retrying
          }
          const delay = Math.min(times * 200, 2000);
          this.logger.warn(`Redis retry attempt ${times}, waiting ${delay}ms`);
          return delay;
        },
        lazyConnect: true,
      });

      // Set up event handlers
      this.redisClient.on("error", (err: Error) => {
        this.logger.error(`Redis connection error: ${err.message}`);
        this.redisAvailable = false;
      });

      this.redisClient.on("close", () => {
        this.logger.warn("Redis connection closed");
        this.redisAvailable = false;
      });

      this.redisClient.on("reconnecting", () => {
        this.logger.info("Redis reconnecting...");
      });

      this.redisClient.on("connect", () => {
        this.redisAvailable = true;
        this.logger.info("Redis connection restored");
      });

      // Connect and verify
      await this.redisClient.connect();
      const pingResult = await this.redisClient.ping();

      if (pingResult === "PONG") {
        this.redisAvailable = true;
        const maskedUrl = this.maskRedisUrl(this.config.redisUrl);
        this.logger.info(`Redis connected: ${maskedUrl}`);
        return true;
      }

      throw new Error(`Unexpected PING response: ${pingResult}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      this.logger.warn(`Redis connection failed: ${errorMessage}. Using file-only mode.`);
      this.redisClient = null;
      this.redisAvailable = false;
      return false;
    }
  }

  /**
   * Load LLM config with cascade resolution
   *
   * @param options - Load options (module, profile, optional scope/userId/version)
   * @returns Resolved LLM config with metadata
   * @throws ConfigNotFoundError if no config found in cascade (should never happen with valid base)
   */
  async loadConfig(options: LoadConfigOptions): Promise<ResolvedLLMConfig> {
    const scope = options.scope ?? this.config.defaultScope;
    const { module, profile } = options;
    let version = options.version ?? 1;
    let forceRefresh = options.forceRefresh ?? false;

    // Normalize userId before building cache key to prevent cache pollution
    // Invalid userIds fall back to "_" to ensure consistent cache keys
    const rawUserId = options.userId ?? "_";
    const userId = validateUserId(rawUserId) ? rawUserId : "_";

    // LH: A/B Experiment Check - BEFORE cache lookup
    // Check Redis for active experiment that may override version
    const experimentResult = await this.getExperimentConfig(module, profile, userId);
    if (experimentResult !== null) {
      version = experimentResult.version;
      forceRefresh = true; // Always bypass cache for experiment configs
      this.logger.info(`[AB] Switched llm:${module}:${profile} to v${version}`);
    }

    // Build primary ConfigId (uses normalized userId and potentially experiment version)
    const configId = this.buildConfigId(scope, module, userId, profile, version);

    // Tier 1: Check LRU cache (skip if forceRefresh)
    if (!forceRefresh) {
      const cached = this.localCache.get(configId);
      if (cached !== null) {
        this.logger.debug(`LRU hit: ${configId}`);
        return JSON.parse(cached) as ResolvedLLMConfig;
      }
    }

    // Tier 2: Check Redis (skip if forceRefresh)
    if (!forceRefresh && this.redisAvailable && this.redisClient) {
      const redisResult = await this.tryLoadFromRedis(configId);
      if (redisResult !== null) {
        // Store in LRU for faster subsequent access
        this.localCache.set(configId, JSON.stringify(redisResult));
        this.logger.debug(`Redis hit: ${configId}`);
        return redisResult;
      }
    }

    // Tier 3: File system with cascade resolution
    const result = await this.resolveWithCascade(scope, module, userId, profile, version);

    // Cache the result (even for experiment configs - cache is per-version)
    const serialized = JSON.stringify(result);
    this.localCache.set(configId, serialized);
    await this.tryStoreInRedis(configId, serialized);

    // Handle deprecated config warning
    if (result.deprecated) {
      this.logger.warn(`Deprecated config loaded: ${result.configId}`);
    }

    return result;
  }

  /**
   * Get cache statistics
   */
  getStats(): CacheStats {
    return {
      localCache: this.localCache.getStats(),
      redisAvailable: this.redisAvailable,
    };
  }

  /**
   * Close all connections and clean up resources
   */
  async close(): Promise<void> {
    this.logger.info("Closing LLMConfigLoader...");

    // Close Redis client
    if (this.redisClient) {
      try {
        await this.redisClient.quit();
      } catch {
        try {
          this.redisClient.disconnect();
        } catch {
          // Ignore
        }
      }
      this.redisClient = null;
      this.redisAvailable = false;
    }

    // Clear local cache
    this.localCache.clear();

    // Clear warned config IDs
    this.warnedConfigIds.clear();

    this.initialized = false;
    this.logger.info("LLMConfigLoader closed");
  }

  // ===========================================================================
  // PRIVATE METHODS
  // ===========================================================================

  /**
   * Build ConfigId from components
   *
   * Format: {scope}:{module}:{userId}:{profile}:v{version}
   */
  private buildConfigId(
    scope: string,
    module: string,
    userId: string,
    profile: string,
    version: number
  ): string {
    return `${scope}:${module}:${userId}:${profile}:v${version}`;
  }

  /**
   * Build Redis key from ConfigId
   */
  private buildRedisKey(configId: string): string {
    return `${REDIS_KEY_PREFIX}${configId}`;
  }

  /**
   * Build Redis key for A/B experiment
   *
   * Format: experiment:llm:{module}:{profile}
   */
  private buildExperimentKey(module: string, profile: string): string {
    return `${EXPERIMENT_KEY_PREFIX}${module}:${profile}`;
  }

  /**
   * Get A/B experiment configuration from Redis
   *
   * Checks if an experiment is active for the given module/profile.
   * 100% toggle only - when enabled, all traffic goes to experiment version.
   *
   * @param module - Module name (e.g., "hrkg")
   * @param profile - Profile name (e.g., "extraction")
   * @param userId - User ID (unused, kept for interface compatibility)
   * @returns ExperimentConfig if experiment is enabled, null otherwise
   */
  private async getExperimentConfig(
    module: string,
    profile: string,
    _userId: string
  ): Promise<ExperimentConfig | null> {
    if (!this.redisAvailable || !this.redisClient) {
      return null;
    }

    try {
      const experimentKey = this.buildExperimentKey(module, profile);
      const content = await this.redisClient.get(experimentKey);

      if (!content) {
        return null;
      }

      // Validate experiment payload before trusting it
      const parsed = JSON.parse(content);
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        typeof parsed.enabled !== "boolean" ||
        !Number.isInteger(parsed.version) ||
        parsed.version < 1 ||
        typeof parsed.enabledAt !== "string"
        // LH: split field validation removed - ignore if present (backward compatible)
      ) {
        this.logger.warn("[AB] Invalid experiment payload, ignoring");
        return null;
      }
      const experiment = parsed as ExperimentConfig;

      // Check if experiment is enabled
      if (!experiment.enabled) {
        return null;
      }

      // LH: Removed traffic splitting (split field) - only 100% toggle now
      // If old Redis keys have split field, just ignore it (backward compatible)

      return experiment;
    } catch (error) {
      // Graceful degradation - log warning and continue with normal flow
      const errorMessage = error instanceof Error ? error.message : String(error);
      this.logger.warn(`[AB] Failed to fetch experiment config: ${errorMessage}`);
      return null;
    }
  }

  /**
   * Generate cascade steps for resolution
   *
   * Order (most specific to least specific):
   * 1. {scope}:{module}:{userId}:{profile}:v{version} - Full specific (user override)
   * 2. {scope}:{module}:_:{profile}:v{version} - Module profile (no user)
   * 3. {scope}:_default:_:{profile}:v{version} - Scope-wide fallback
   * 4. _default:_default:_:{profile}:v{version} - Global profile fallback
   * 5. _default:_default:_:_base:v1 - Ultimate base fallback
   */
  private generateCascadeSteps(
    scope: string,
    module: string,
    userId: string,
    profile: string,
    version: number
  ): CascadeStep[] {
    const steps: CascadeStep[] = [];

    // Step 1: Full specific (only if userId is provided and not "_")
    if (userId !== "_") {
      steps.push({ scope, module, userId, profile, version });
    }

    // Step 2: Module profile (no user)
    steps.push({ scope, module, userId: "_", profile, version });

    // Step 3: Scope-wide fallback (only if module is not _default)
    if (module !== "_default") {
      steps.push({ scope, module: "_default", userId: "_", profile, version });
    }

    // Step 4: Global profile fallback (only if scope is not _default)
    if (scope !== "_default") {
      steps.push({ scope: "_default", module: "_default", userId: "_", profile, version });
    }

    // Step 5: Ultimate base fallback (only if profile is not _base or version is not 1)
    if (profile !== "_base" || version !== 1) {
      steps.push({
        scope: "_default",
        module: "_default",
        userId: "_",
        profile: "_base",
        version: 1,
      });
    }

    return steps;
  }

  /**
   * Resolve config using cascade
   *
   * Tries each cascade step in order, returning first successful load.
   */
  private async resolveWithCascade(
    scope: string,
    module: string,
    userId: string,
    profile: string,
    version: number
  ): Promise<ResolvedLLMConfig> {
    // Validate inputs
    validateScope(scope);
    validateModule(module);
    validateProfile(profile);
    validateVersion(version);
    // userId validation is soft - invalid IDs fall back to "_"
    const validUserId = validateUserId(userId) ? userId : "_";

    const steps = this.generateCascadeSteps(scope, module, validUserId, profile, version);
    const originalConfigId = this.buildConfigId(scope, module, validUserId, profile, version);

    for (const step of steps) {
      try {
        // Note: loadConfigFromFile uses module and profile for file path
        // scope is not part of file path (used for cascade resolution logic)
        const config = await loadConfigFromFile(
          this.config.configDir,
          step.module,
          step.profile,
          step.version
        );

        const resolvedConfigId = this.buildConfigId(
          step.scope,
          step.module,
          step.userId,
          step.profile,
          step.version
        );

        // Log rate-limited warning if fell back to _base
        if (step.profile === "_base" && profile !== "_base") {
          this.logFallbackWarning(originalConfigId, profile, "_base");
        }

        // Check for _low fallback pattern (HRKG use case)
        if (profile.endsWith("_low") && step.profile !== profile) {
          const baseProfile = profile.slice(0, -4); // Remove "_low"
          if (step.profile === baseProfile) {
            this.logFallbackWarning(originalConfigId, profile, baseProfile);
          }
        }

        return {
          ...config,
          configId: resolvedConfigId,
          scope: step.scope,
          module: step.module,
          profile: step.profile,
          version: step.version,
        };
      } catch (error) {
        if (error instanceof ConfigNotFoundError) {
          // Try next cascade step
          continue;
        }
        // Propagate other errors
        throw error;
      }
    }

    // Should never reach here if _default/_base.v1.yaml exists
    throw new ConfigNotFoundError(
      `Config not found after cascade resolution: ${originalConfigId}. ` +
        `Checked: ${steps.map((s) => this.buildConfigId(s.scope, s.module, s.userId, s.profile, s.version)).join(", ")}`
    );
  }

  /**
   * Log rate-limited fallback warning
   *
   * Only logs once per configId to avoid log spam.
   */
  private logFallbackWarning(
    configId: string,
    requestedProfile: string,
    fallbackProfile: string
  ): void {
    if (this.warnedConfigIds.has(configId)) {
      return;
    }

    // Evict oldest if at capacity (simple FIFO approximation using Set iteration order)
    if (this.warnedConfigIds.size >= MAX_WARNED_CONFIG_IDS) {
      const oldest = this.warnedConfigIds.values().next().value;
      if (oldest) {
        this.warnedConfigIds.delete(oldest);
      }
    }

    this.warnedConfigIds.add(configId);
    this.logger.warn(
      `LLMConfig fallback: ${requestedProfile} not found, using ${fallbackProfile} (configId: ${configId})`
    );
  }

  /**
   * Try to load config from Redis
   *
   * @returns ResolvedLLMConfig or null if not found/error
   */
  private async tryLoadFromRedis(configId: string): Promise<ResolvedLLMConfig | null> {
    if (!this.redisClient) {
      return null;
    }

    try {
      const redisKey = this.buildRedisKey(configId);
      const content = await this.redisClient.get(redisKey);

      if (content) {
        return JSON.parse(content) as ResolvedLLMConfig;
      }

      return null;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      this.logger.warn(
        `Redis fetch failed for ${configId}: ${errorMessage}. Falling back to file.`
      );
      // Graceful degradation - continue to file system
      return null;
    }
  }

  /**
   * Try to store config in Redis (best effort)
   */
  private async tryStoreInRedis(configId: string, serialized: string): Promise<void> {
    if (!this.redisAvailable || !this.redisClient) {
      return;
    }

    try {
      const redisKey = this.buildRedisKey(configId);
      await this.redisClient.setex(redisKey, this.config.redisTtlSeconds, serialized);
      this.logger.debug(`Stored in Redis: ${redisKey}`);
    } catch (error) {
      this.logger.debug(`Failed to store in Redis (non-critical): ${error}`);
    }
  }

  /**
   * Mask Redis URL for logging (hide password)
   */
  private maskRedisUrl(url: string): string {
    try {
      const parsed = new URL(url);
      if (parsed.password) {
        parsed.password = "****";
      }
      return parsed.toString();
    } catch {
      // If parsing fails, do basic masking
      return url.replace(/:([^@]+)@/, ":****@");
    }
  }
}

// =============================================================================
// FACTORY FUNCTION
// =============================================================================

/**
 * Create a new LLMConfigLoader instance
 *
 * Convenience factory function for creating LLMConfigLoader instances.
 *
 * @param config - Loader configuration
 * @returns New LLMConfigLoader instance
 *
 * @example
 * ```typescript
 * const loader = createLLMConfigLoader({
 *   configDir: '/app/config/llm',
 *   redisUrl: process.env.REDIS_KV_URL,
 * });
 *
 * // Initialize (validates base config, connects Redis)
 * await loader.init();
 *
 * // Load a config
 * const config = await loader.loadConfig({ module: 'hrkg', profile: 'extraction' });
 * ```
 */
export function createLLMConfigLoader(config: LLMConfigLoaderConfig): LLMConfigLoader {
  return new LLMConfigLoader(config);
}
