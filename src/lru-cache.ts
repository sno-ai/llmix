/**
 * LRU Cache with TTL Support
 *
 * Thread-safe (in async context) LRU cache implementation.
 * Port of Python LRUCache from lib/prompt/prompt_redis_loader.py
 */

import type { LRUCacheStats } from "./types";

// =============================================================================
// LRU CACHE ENTRY
// =============================================================================

interface CacheEntry {
  value: string;
  timestamp: number;
}

// =============================================================================
// LRU CACHE CLASS
// =============================================================================

/**
 * LRU Cache with TTL support
 *
 * Uses Map which maintains insertion order, allowing efficient LRU eviction.
 * TypeScript/Node.js is single-threaded for synchronous ops, so no explicit
 * locking is needed unlike the Python implementation.
 *
 * @example
 * ```typescript
 * const cache = new LRUCache(100, 21600);
 * cache.set('key', 'value');
 * const value = cache.get('key'); // 'value' or null
 * ```
 */
export class LRUCache {
  private readonly maxSize: number;
  private readonly ttlMs: number;
  private readonly cache: Map<string, CacheEntry>;
  private hits = 0;
  private misses = 0;

  /**
   * Create a new LRU cache
   *
   * @param maxSize - Maximum number of entries (default: 100)
   * @param ttlSeconds - Time-to-live in seconds (default: 21600 = 6 hours)
   */
  constructor(maxSize = 100, ttlSeconds = 21600) {
    this.maxSize = maxSize;
    this.ttlMs = ttlSeconds * 1000;
    this.cache = new Map();
  }

  /**
   * Get value from cache if exists and not expired
   *
   * @param key - Cache key
   * @returns Value or null if not found/expired
   */
  get(key: string): string | null {
    const entry = this.cache.get(key);

    if (!entry) {
      this.misses++;
      return null;
    }

    // Check TTL
    const age = Date.now() - entry.timestamp;
    if (age > this.ttlMs) {
      this.cache.delete(key);
      this.misses++;
      return null;
    }

    // Move to end (most recently used) - Map maintains insertion order
    this.cache.delete(key);
    this.cache.set(key, entry);
    this.hits++;

    return entry.value;
  }

  /**
   * Set value in cache with current timestamp
   *
   * @param key - Cache key
   * @param value - Value to cache
   */
  set(key: string, value: string): void {
    // If key exists, delete first to update position
    if (this.cache.has(key)) {
      this.cache.delete(key);
    }

    // Add to end
    this.cache.set(key, {
      value,
      timestamp: Date.now(),
    });

    // Evict oldest if over limit
    if (this.cache.size > this.maxSize) {
      // Map iterates in insertion order, first key is oldest
      const oldestKey = this.cache.keys().next().value;
      if (oldestKey) {
        this.cache.delete(oldestKey);
      }
    }
  }

  /**
   * Invalidate cache entries matching pattern
   *
   * Cache key format: `{scope}:{module}:{userId}:{profile}:v{version}`
   * Example key: `default:hrkg:_:extraction:v1`
   *
   * Pattern matching (colon-separated, supports `*` wildcards):
   * - "*" -> Clear all entries
   * - "default:*" -> Clear all default scope entries
   * - "default:hrkg:*" -> Clear all hrkg module entries in default scope
   * - "*:*:user123:*" -> Clear all entries for specific user
   *
   * @param pattern - Invalidation pattern
   * @returns Number of entries invalidated
   */
  invalidate(pattern: string): number {
    // Clear all
    if (pattern === "*") {
      const count = this.cache.size;
      this.cache.clear();
      return count;
    }

    // Pattern matching
    const patternParts = pattern.split(":");
    const keysToDelete: string[] = [];

    for (const key of this.cache.keys()) {
      const keyParts = key.split(":");
      let match = true;

      for (let i = 0; i < patternParts.length; i++) {
        const patternPart = patternParts[i];

        // Key must have a part at this position (wildcards don't make parts optional)
        if (i >= keyParts.length) {
          match = false;
          break;
        }

        // Wildcard matches any existing part
        if (patternPart === "*") {
          continue;
        }

        // Pattern part doesn't match key part
        if (keyParts[i] !== patternPart) {
          match = false;
          break;
        }
      }

      if (match) {
        keysToDelete.push(key);
      }
    }

    for (const key of keysToDelete) {
      this.cache.delete(key);
    }

    return keysToDelete.length;
  }

  /**
   * Check if key exists and is not expired
   *
   * @param key - Cache key
   * @returns True if key exists and not expired
   */
  has(key: string): boolean {
    const entry = this.cache.get(key);
    if (!entry) {
      return false;
    }

    const age = Date.now() - entry.timestamp;
    if (age > this.ttlMs) {
      this.cache.delete(key);
      return false;
    }

    return true;
  }

  /**
   * Delete a specific key from cache
   *
   * @param key - Cache key
   * @returns True if key existed
   */
  delete(key: string): boolean {
    return this.cache.delete(key);
  }

  /**
   * Clear all cache entries
   */
  clear(): void {
    this.cache.clear();
  }

  /**
   * Get cache statistics
   *
   * @returns Cache statistics
   */
  getStats(): LRUCacheStats {
    const total = this.hits + this.misses;
    const hitRate = total > 0 ? (this.hits / total) * 100 : 0;

    return {
      size: this.cache.size,
      maxSize: this.maxSize,
      hits: this.hits,
      misses: this.misses,
      hitRate,
    };
  }

  /**
   * Reset statistics counters
   */
  resetStats(): void {
    this.hits = 0;
    this.misses = 0;
  }
}
