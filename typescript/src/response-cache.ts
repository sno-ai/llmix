/**
 * Two-Tier Response Cache for LLMix
 *
 * L1: In-memory LRU with TTL (lru-cache npm package)
 * L2: Redis with TTL via SETEX (ioredis, optional)
 *
 * Cache key: SHA-256 of canonical JSON (sorted keys, deterministic).
 * Prefix: "llmix2:resp:"
 *
 * Three strategies:
 * - "redis": strict — fail if no REDIS_URL
 * - "redis-or-memory": best-effort — degrade to L1 only with warning
 * - "memory": L1 only, no Redis attempted
 */

import { createHash } from "node:crypto";
import { LRUCache as LRUCacheLib } from "lru-cache";
import { lazyImport } from "./lazy-import";
import type { CacheHitTier, CachingStrategy, ResponseCacheStrategy } from "./types";

// Lazy ioredis import — don't fail if not installed
const getIoredis = lazyImport<typeof import("ioredis")>("ioredis", "ioredis");

// =============================================================================
// CONSTANTS
// =============================================================================

const CACHE_KEY_PREFIX = "llmix2:resp:";
const DEFAULT_L1_MAX = 1000;
const DEFAULT_TTL_SECONDS = 3600;
const DEFAULT_L2_TTL_SECONDS = 3600;
const HEALTH_PING_INTERVAL_MS = 30_000;

/** Strategies that activate the two-tier response cache. */
const RESPONSE_CACHE_STRATEGIES: ReadonlySet<string> = new Set([
  "redis",
  "redis-or-memory",
  "memory",
]);

/** Strategies that skip the response cache entirely. */
const SKIP_STRATEGIES: ReadonlySet<string> = new Set([
  "native",
  "gateway",
  "disabled",
]);

// =============================================================================
// CACHE KEY FIELDS (included in hash)
// =============================================================================

/** Fields included in cache key generation, in canonical order. */
const CACHE_KEY_FIELDS = [
  "baseUrl",
  "maxOutputTokens",
  "messages",
  "model",
  "provider",
  "providerOptions",
  "responseFormat",
  "temperature",
] as const;

// =============================================================================
// TYPES
// =============================================================================

export interface TwoTierCacheConfig {
  /** Max L1 entries (default: 1000) */
  maxItems?: number;
  /** TTL in seconds for both L1 and L2 (default: 3600) */
  ttlSeconds?: number;
  /** Redis URL for L2. Required for "redis" strategy. */
  redisUrl?: string;
  /** Logger (defaults to console) */
  logger?: CacheLogger;
}

interface CacheLogger {
  debug(message: string, ...args: unknown[]): void;
  info(message: string, ...args: unknown[]): void;
  warn(message: string, ...args: unknown[]): void;
  error(message: string, ...args: unknown[]): void;
}

export interface ResponseCacheStats {
  l1Size: number;
  l1Max: number;
  l2Enabled: boolean;
  l2Healthy: boolean;
  strategy: ResponseCacheStrategy;
}

/** Parameters used for cache key generation. */
export interface CacheKeyParams {
  provider: string;
  model: string;
  messages: unknown[];
  baseUrl?: string | null;
  temperature?: number | null;
  maxOutputTokens?: number | null;
  responseFormat?: unknown | null;
  providerOptions?: Record<string, unknown> | null;
}

interface CachedValue {
  data: string;
  cachedAt: number;
}

// =============================================================================
// STRATEGY HELPERS
// =============================================================================

/** Check if a caching strategy activates the two-tier response cache. */
export function isResponseCacheStrategy(
  strategy: CachingStrategy,
): strategy is ResponseCacheStrategy {
  return RESPONSE_CACHE_STRATEGIES.has(strategy);
}

/** Check if a caching strategy should skip the response cache. */
export function shouldSkipCache(strategy: CachingStrategy): boolean {
  return SKIP_STRATEGIES.has(strategy);
}

/**
 * Resolve the effective response cache strategy.
 * Returns null if the strategy doesn't activate the response cache.
 */
export function resolveResponseCacheStrategy(
  strategy: CachingStrategy,
  redisUrl: string | undefined,
  logger?: CacheLogger,
): ResponseCacheStrategy | null {
  if (!isResponseCacheStrategy(strategy)) {
    return null;
  }

  if (strategy === "redis" && !redisUrl) {
    throw new Error(
      'Response cache strategy "redis" requires REDIS_URL to be set.',
    );
  }

  if (strategy === "redis-or-memory" && !redisUrl) {
    (logger ?? console).warn(
      '[llmix] Response cache strategy "redis-or-memory": REDIS_URL not set, degrading to L1 only.',
    );
    return "memory";
  }

  return strategy;
}

// =============================================================================
// CACHE KEY GENERATION
// =============================================================================

/**
 * Generate a deterministic SHA-256 cache key from request parameters.
 *
 * - Canonical JSON with sorted keys
 * - Null/undefined fields excluded
 * - Prefixed with "llmix2:resp:"
 */
export function generateCacheKey(params: CacheKeyParams): string {
  const canonical: Record<string, unknown> = {};

  for (const field of CACHE_KEY_FIELDS) {
    const value = params[field as keyof CacheKeyParams];
    if (value !== undefined && value !== null) {
      canonical[field] = value;
    }
  }

  const json = JSON.stringify(canonical, sortReplacer);
  const hash = createHash("sha256").update(json).digest("hex");
  return `${CACHE_KEY_PREFIX}${hash}`;
}

/**
 * JSON replacer that sorts object keys at every level for deterministic serialization.
 */
function sortReplacer(_key: string, value: unknown): unknown {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      sorted[k] = (value as Record<string, unknown>)[k];
    }
    return sorted;
  }
  return value;
}

// =============================================================================
// TWO-TIER CACHE
// =============================================================================

export class TwoTierCache {
  private readonly l1: LRUCacheLib<string, CachedValue>;
  private readonly strategy: ResponseCacheStrategy;
  private readonly ttlSeconds: number;
  private readonly log: CacheLogger;

  // L2 state
  private redisClient: InstanceType<typeof import("ioredis").default> | null = null;
  private l2Enabled: boolean;
  private l2Healthy = true;
  private l2ConsecutiveWriteFailures = 0;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private redisUrl: string | undefined;

  constructor(strategy: ResponseCacheStrategy, config: TwoTierCacheConfig = {}) {
    this.strategy = strategy;
    this.ttlSeconds = config.ttlSeconds ?? DEFAULT_TTL_SECONDS;
    this.log = config.logger ?? {
      debug: (msg, ...args) => console.debug(`[llmix:cache] ${msg}`, ...args),
      info: (msg, ...args) => console.info(`[llmix:cache] ${msg}`, ...args),
      warn: (msg, ...args) => console.warn(`[llmix:cache] ${msg}`, ...args),
      error: (msg, ...args) => console.error(`[llmix:cache] ${msg}`, ...args),
    };
    this.redisUrl = config.redisUrl;

    // L1 setup
    const maxItems = config.maxItems ?? DEFAULT_L1_MAX;
    this.l1 = new LRUCacheLib<string, CachedValue>({
      max: maxItems,
      ttl: this.ttlSeconds * 1000,
    });

    // L2 enabled when strategy requires Redis and URL is available
    this.l2Enabled = strategy !== "memory" && !!config.redisUrl;
  }

  /** Lazily connect to Redis on first L2 operation. */
  private async ensureRedis(): Promise<boolean> {
    if (!this.l2Enabled) return false;
    if (this.redisClient) return this.l2Healthy;

    try {
      const ioredisModule = await getIoredis();
      const Redis = ioredisModule.default;
      this.redisClient = new Redis(this.redisUrl!, {
        lazyConnect: true,
        maxRetriesPerRequest: 1,
        connectTimeout: 5000,
        commandTimeout: 3000,
      });
      await this.redisClient.connect();
      this.startHealthMonitor();
      this.log.info("Redis L2 connected for response cache.");
      return true;
    } catch (err) {
      this.log.warn("Failed to connect Redis L2, operating L1-only.", err);
      this.l2Healthy = false;
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // GET
  // ---------------------------------------------------------------------------

  async get(key: string): Promise<{ value: string; tier: CacheHitTier } | null> {
    // L1 lookup
    const l1Entry = this.l1.get(key);
    if (l1Entry) {
      return { value: l1Entry.data, tier: "l1" };
    }

    // L2 lookup
    if (!this.l2Enabled || !this.l2Healthy) return null;

    try {
      const connected = await this.ensureRedis();
      if (!connected || !this.redisClient) return null;

      const raw = await this.redisClient.get(key);
      if (!raw) return null;

      // Backfill L1
      const parsed: CachedValue = JSON.parse(raw);
      this.l1.set(key, parsed);

      return { value: parsed.data, tier: "l2" };
    } catch (err) {
      this.log.warn("L2 GET failed, treating as cache miss.", err);
      return null;
    }
  }

  // ---------------------------------------------------------------------------
  // SET
  // ---------------------------------------------------------------------------

  async set(key: string, value: string): Promise<void> {
    const entry: CachedValue = { data: value, cachedAt: Date.now() };

    // L1 write (synchronous)
    this.l1.set(key, entry);

    // L2 write (fire-and-forget)
    if (this.l2Enabled && this.l2Healthy) {
      this.writeL2(key, entry).catch((err) => {
        this.log.warn("L2 SET failed (fire-and-forget).", err);
      });
    }
  }

  private async writeL2(key: string, entry: CachedValue): Promise<void> {
    const connected = await this.ensureRedis();
    if (!connected || !this.redisClient) return;

    try {
      const serialized = JSON.stringify(entry);
      const ttl = this.ttlSeconds > 0 ? this.ttlSeconds : DEFAULT_L2_TTL_SECONDS;
      await this.redisClient.setex(key, ttl, serialized);
      this.l2ConsecutiveWriteFailures = 0;
    } catch (err) {
      this.l2ConsecutiveWriteFailures++;
      if (this.l2ConsecutiveWriteFailures >= 3) {
        this.l2Healthy = false;
        this.log.warn("L2 marked unhealthy after 3 consecutive write failures.");
      }
      throw err;
    }
  }

  // ---------------------------------------------------------------------------
  // HEALTH MONITORING
  // ---------------------------------------------------------------------------

  private startHealthMonitor(): void {
    if (this.healthTimer) return;

    this.healthTimer = setInterval(() => {
      this.pingRedis().catch(() => {
        /* handled inside */
      });
    }, HEALTH_PING_INTERVAL_MS);

    // Don't keep process alive just for health pings
    if (this.healthTimer && typeof this.healthTimer === "object" && "unref" in this.healthTimer) {
      this.healthTimer.unref();
    }
  }

  private async pingRedis(): Promise<void> {
    if (!this.redisClient) return;

    try {
      await this.redisClient.ping();
      if (!this.l2Healthy) {
        this.l2Healthy = true;
        this.log.info("Redis L2 recovered.");
      }
    } catch {
      if (this.l2Healthy) {
        this.l2Healthy = false;
        this.log.warn("Redis L2 health check failed, disabling L2 temporarily.");
      }
    }
  }

  // ---------------------------------------------------------------------------
  // LIFECYCLE
  // ---------------------------------------------------------------------------

  /** Get cache statistics. */
  getStats(): ResponseCacheStats {
    return {
      l1Size: this.l1.size,
      l1Max: this.l1.max,
      l2Enabled: this.l2Enabled,
      l2Healthy: this.l2Healthy,
      strategy: this.strategy,
    };
  }

  /** Clear L1 cache. Does not clear L2 (Redis manages its own TTL). */
  clear(): void {
    this.l1.clear();
  }

  /** Shut down: stop health monitor, disconnect Redis. */
  async close(): Promise<void> {
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
      this.healthTimer = null;
    }
    if (this.redisClient) {
      try {
        await this.redisClient.quit();
      } catch {
        // Best-effort disconnect
      }
      this.redisClient = null;
    }
  }
}
