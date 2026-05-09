/**
 * Response cache unit tests.
 * Consumes shared test vectors from fixtures/cache-key-vectors.json.
 *
 * Tests:
 * - Cache key determinism (shared vectors)
 * - Cache key collision avoidance
 * - L1 hit/miss/eviction
 * - Strategy resolution
 * - Cache skip rules
 *
 * Run with: bun run tests/typescript/response-cache.test.ts
 */
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import {
	generateCacheKey,
	TwoTierCache,
	resolveResponseCacheStrategy,
	isResponseCacheStrategy,
	type CacheKeyParams,
} from "../../typescript/src/response-cache.js"

// =============================================================================
// HELPERS
// =============================================================================

interface TestVector {
	name: string
	description?: string
	input: CacheKeyParams
	expectedKey: string
}

interface VectorsFile {
	description: string
	prefix: string
	vectors: TestVector[]
}

const fixtureDir = resolve(import.meta.dirname, "..", "fixtures")
const vectors: VectorsFile = JSON.parse(
	readFileSync(resolve(fixtureDir, "cache-key-vectors.json"), "utf-8"),
)

let passed = 0
let failed = 0

function assert(condition: boolean, msg: string) {
	if (condition) {
		passed++
		console.log(`+ ${msg}`)
	} else {
		failed++
		console.log(`x ${msg}`)
	}
}

function assertEqual(actual: unknown, expected: unknown, msg: string) {
	assert(actual === expected, `${msg}: got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`)
}

// =============================================================================
// CACHE KEY TESTS (shared vectors)
// =============================================================================

console.log("--- Cache Key Determinism (shared vectors) ---")

for (const vec of vectors.vectors) {
	const key = generateCacheKey(vec.input)
	assertEqual(key, vec.expectedKey, `[${vec.name}] cache key`)
}

// =============================================================================
// CACHE KEY COLLISION AVOIDANCE
// =============================================================================

console.log("\n--- Cache Key Collision Avoidance ---")

// Collect all unique keys and verify no unwanted collisions
const keyMap = new Map<string, string>()
const expectedCollisions = new Set(["same-params-different-order", "null-and-undefined-fields-excluded"])

for (const vec of vectors.vectors) {
	const key = generateCacheKey(vec.input)

	if (keyMap.has(key)) {
		const other = keyMap.get(key)!
		const isExpected = expectedCollisions.has(vec.name)
		assert(isExpected, `[${vec.name}] key collision with ${other} (expected: ${isExpected})`)
	} else {
		keyMap.set(key, vec.name)
	}
}

// Verify prefix
for (const vec of vectors.vectors) {
	const key = generateCacheKey(vec.input)
	assert(key.startsWith("llmix:resp:"), `[${vec.name}] has correct prefix`)
}

// =============================================================================
// L1 HIT / MISS / EVICTION
// =============================================================================

console.log("\n--- L1 Hit / Miss / Eviction ---")

{
	const cache = new TwoTierCache("memory", { maxItems: 3, ttlSeconds: 60 })

	// Miss on empty cache
	const miss = await cache.get("key1")
	assertEqual(miss, null, "empty cache returns null")

	// Set and hit
	await cache.set("key1", "value1")
	const hit = await cache.get("key1")
	assert(hit !== null, "L1 hit after set")
	assertEqual(hit?.value, "value1", "L1 hit returns correct value")
	assertEqual(hit?.tier, "l1", "L1 hit reports tier l1")

	// Multiple entries
	await cache.set("key2", "value2")
	await cache.set("key3", "value3")

	const stats = cache.getStats()
	assertEqual(stats.l1Size, 3, "L1 has 3 entries")
	assertEqual(stats.l1Max, 3, "L1 max is 3")
	assertEqual(stats.l2Enabled, false, "L2 disabled for memory strategy")
	assertEqual(stats.strategy, "memory", "strategy is memory")

	// Eviction: adding 4th item should evict oldest (LRU)
	await cache.set("key4", "value4")
	const evictedStats = cache.getStats()
	assertEqual(evictedStats.l1Size, 3, "L1 size stays at max after eviction")

	// key1 should have been evicted (LRU - least recently used)
	const evicted = await cache.get("key1")
	assertEqual(evicted, null, "evicted key returns null")

	// key4 should be present
	const newest = await cache.get("key4")
	assert(newest !== null, "newest key is present after eviction")
	assertEqual(newest?.value, "value4", "newest key has correct value")

	// Clear
	cache.clear()
	const cleared = await cache.get("key4")
	assertEqual(cleared, null, "cache clear removes all entries")
	assertEqual(cache.getStats().l1Size, 0, "L1 size is 0 after clear")

	await cache.close()
}

// =============================================================================
// STRATEGY RESOLUTION
// =============================================================================

console.log("\n--- Strategy Resolution ---")

// isResponseCacheStrategy
assert(isResponseCacheStrategy("redis") === true, '"redis" is response cache strategy')
assert(isResponseCacheStrategy("redis-or-memory") === true, '"redis-or-memory" is response cache strategy')
assert(isResponseCacheStrategy("memory") === true, '"memory" is response cache strategy')
assert(isResponseCacheStrategy("native") === false, '"native" is not response cache strategy')
assert(isResponseCacheStrategy("gateway") === false, '"gateway" is not response cache strategy')
assert(isResponseCacheStrategy("disabled") === false, '"disabled" is not response cache strategy')

// resolveResponseCacheStrategy
assertEqual(
	resolveResponseCacheStrategy("redis", "redis://localhost:6379"),
	"redis",
	'resolve "redis" with URL',
)

try {
	resolveResponseCacheStrategy("redis", undefined)
	assert(false, 'resolve "redis" without URL should throw')
} catch (e) {
	assert(e instanceof Error && e.message.includes("REDIS_URL"), 'resolve "redis" without URL throws')
}

assertEqual(
	resolveResponseCacheStrategy("redis-or-memory", "redis://localhost:6379"),
	"redis-or-memory",
	'resolve "redis-or-memory" with URL',
)

assertEqual(
	resolveResponseCacheStrategy("redis-or-memory", undefined),
	"memory",
	'resolve "redis-or-memory" without URL degrades to memory',
)

assertEqual(
	resolveResponseCacheStrategy("memory", undefined),
	"memory",
	'resolve "memory" without URL',
)

assertEqual(
	resolveResponseCacheStrategy("native", undefined),
	null,
	'resolve "native" returns null',
)

assertEqual(
	resolveResponseCacheStrategy("disabled", undefined),
	null,
	'resolve "disabled" returns null',
)

// =============================================================================
// REDIS INTEGRATION TESTS (stubs with TODO)
// =============================================================================

console.log("\n--- Redis Integration Tests (stubs) ---")

// TODO: Task 134 - Redis integration tests
// These require a running Redis instance.
// Stub: verify TwoTierCache can be created with redis strategy
{
	const cache = new TwoTierCache("redis", {
		redisUrl: "redis://localhost:6379",
		maxItems: 100,
		ttlSeconds: 60,
	})
	const stats = cache.getStats()
	assertEqual(stats.l2Enabled, true, "L2 enabled for redis strategy with URL")
	assertEqual(stats.l2Healthy, true, "L2 healthy before first use")
	// Don't actually connect — just verify configuration
	await cache.close()
}

console.log("TODO: Redis L2 hit/miss tests require running Redis")
console.log("TODO: Redis health monitoring tests require running Redis")
console.log("TODO: Redis L2 backfill tests require running Redis")

// Focused parity regressions without a live Redis instance
{
	const cache = new TwoTierCache("redis", {
		redisUrl: "redis://localhost:6379",
		maxItems: 10,
		ttlSeconds: 60,
	})
	let serializedPayload = ""
	;(cache as unknown as Record<string, unknown>)["ensureRedis"] = async () => true
	;(cache as unknown as Record<string, unknown>)["redisClient"] = {
		setex: async (_key: string, _ttl: number, payload: string) => {
			serializedPayload = payload
		},
		get: async () => null,
		quit: async () => {},
		ping: async () => "PONG",
	}

	await cache.set("redis-seconds", "value")
	await new Promise((resolve) => setTimeout(resolve, 0))

	const payload = JSON.parse(serializedPayload) as { cached_at: number; data: string }
	assertEqual(payload.data, "value", "Redis payload preserves cached data")
	assert(payload.cached_at < 1_000_000_000_000, "Redis payload stores cached_at in seconds")
	assert(payload.cached_at > 1_000_000_000, "Redis payload stores a current timestamp in seconds")

	await cache.close()
}

{
	const cache = new TwoTierCache("redis", {
		redisUrl: "redis://localhost:6379",
		maxItems: 10,
		ttlSeconds: 60,
	})
	const legacyCachedAtMs = Date.now()
	;(cache as unknown as Record<string, unknown>)["ensureRedis"] = async () => true
	;(cache as unknown as Record<string, unknown>)["redisClient"] = {
		get: async () => JSON.stringify({ data: "legacy-value", cached_at: legacyCachedAtMs }),
		setex: async () => {},
		quit: async () => {},
		ping: async () => "PONG",
	}

	const hit = await cache.get("legacy-ms")
	assert(hit !== null, "Redis GET accepts legacy millisecond timestamps")
	assertEqual(hit?.value, "legacy-value", "Redis GET returns legacy payload value")
	assertEqual(hit?.tier, "l2", "Redis GET reports L2 hit for legacy payload")

	await cache.close()
}

// =============================================================================
// SUMMARY
// =============================================================================

console.log(`\n=== Response Cache Tests: ${passed} passed, ${failed} failed ===`)
if (failed > 0) {
	process.exit(1)
}
