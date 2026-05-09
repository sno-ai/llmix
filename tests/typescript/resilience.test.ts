/**
 * Tests for the LLMix resilience module (TypeScript).
 *
 * Covers circuit breaker, kill switch, singleflight, and retry logic.
 * Uses shared fixtures from tests/fixtures/circuit-breaker-scenarios.json.
 */

import { readFileSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  CircuitBreaker,
  CircuitOpenError,
  CircuitState,
  KillSwitch,
  KillSwitchActiveError,
  RetryPolicy,
  Singleflight,
  calculateDelay,
  isRetryable,
  parseRetryAfter,
} from "../../typescript/src/resilience.js";

const fixtureDir = resolve(import.meta.dirname, "..", "fixtures");
const scenariosPath = resolve(fixtureDir, "circuit-breaker-scenarios.json");

interface Scenario {
  retryableStatusCodes: number[];
  nonRetryableStatusCodes: number[];
  retryDelayScenarios: {
    attempts: { attempt: number; minMs: number; maxMs: number }[];
  };
}

const scenarios: Scenario = JSON.parse(readFileSync(scenariosPath, "utf-8"));

let passed = 0;
let failed = 0;

function assert(condition: boolean, msg: string): void {
  if (condition) {
    passed++;
    console.log(`[PASS] ${msg}`);
  } else {
    failed++;
    console.log(`[FAIL] ${msg}`);
  }
}

function assertEqual<T>(actual: T, expected: T, msg: string): void {
  if (actual === expected) {
    assert(true, msg);
  } else {
    assert(false, `${msg}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function makeTmpDir(): string {
  const dir = join(tmpdir(), `llmix-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

// ---------------------------------------------------------------------------
// Circuit Breaker Tests
// ---------------------------------------------------------------------------

function testCircuitBreakerClosedToOpen(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  assertEqual(cb.state, CircuitState.CLOSED, "CB starts CLOSED");
  cb.onFailure(500);
  cb.onFailure(502);
  assertEqual(cb.state, CircuitState.CLOSED, "CB still CLOSED after 2 failures");
  cb.onFailure(503);
  assertEqual(cb.state, CircuitState.OPEN, "CB OPEN after 3 failures");
}

function testCircuitBreakerAuthErrorsIgnored(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  for (let i = 0; i < 10; i++) {
    cb.onFailure(401);
    cb.onFailure(403);
  }
  assertEqual(cb.state, CircuitState.CLOSED, "401/403 do NOT trip breaker");
}

function testCircuitBreakerNonRetryableIgnored(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  cb.onFailure(400);
  cb.onFailure(404);
  cb.onFailure(422);
  assertEqual(cb.state, CircuitState.CLOSED, "4xx (non-429) do NOT trip breaker");
}

function testCircuitBreakerSuccessResets(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onSuccess();
  cb.onFailure(500);
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.CLOSED, "Success resets consecutive failure counter");
}

function testCircuitBreakerOpenBlocksCheck(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  try {
    cb.check();
    assert(false, "OPEN should throw CircuitOpenError");
  } catch (err) {
    assert(err instanceof CircuitOpenError, "OPEN throws CircuitOpenError on check()");
  }
}

async function testCircuitBreakerHalfOpenToClosed(): Promise<void> {
  const cb = new CircuitBreaker("openai", "https://api.openai.com", { cooldownMs: 10, permittedHalfOpenCalls: 1 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.OPEN, "CB is OPEN");
  await new Promise((r) => setTimeout(r, 20));
  assertEqual(cb.state, CircuitState.HALF_OPEN, "CB transitions to HALF_OPEN after cooldown");
  cb.check(); // Allow probe
  cb.onSuccess();
  assertEqual(cb.state, CircuitState.CLOSED, "HALF_OPEN -> CLOSED on success");
}

async function testCircuitBreakerHalfOpenToOpen(): Promise<void> {
  const cb = new CircuitBreaker("openai", "https://api.openai.com", { cooldownMs: 10, permittedHalfOpenCalls: 1 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  assertEqual(cb.state, CircuitState.HALF_OPEN, "CB is HALF_OPEN");
  cb.check(); // Allow probe
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.OPEN, "HALF_OPEN -> OPEN on failure");
}

async function testCircuitBreakerHalfOpenBlocksSecondProbe(): Promise<void> {
  const cb = new CircuitBreaker("openai", "https://api.openai.com", { cooldownMs: 10, permittedHalfOpenCalls: 1 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  cb.check(); // First probe allowed
  try {
    cb.check(); // Second probe blocked
    assert(false, "Second probe in HALF_OPEN should throw");
  } catch (err) {
    assert(err instanceof CircuitOpenError, "HALF_OPEN blocks second concurrent probe");
  }
}

async function testCircuitBreakerHalfOpenBlocksWhenFull(): Promise<void> {
  const cb = new CircuitBreaker("openai", "https://api.openai.com", { cooldownMs: 10, permittedHalfOpenCalls: 2 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  cb.check(); // Probe 1 allowed
  cb.check(); // Probe 2 allowed
  try {
    cb.check(); // Probe 3 blocked (only 2 permitted)
    assert(false, "Excess probe in HALF_OPEN should throw");
  } catch (err) {
    assert(err instanceof CircuitOpenError, "HALF_OPEN blocks when all probe slots full");
  }
}

async function testCircuitBreakerMultiProbeRecovery(): Promise<void> {
  const cb = new CircuitBreaker("sno-gpu", "http://gpu:8080", { cooldownMs: 10, permittedHalfOpenCalls: 3 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  // Allow 3 probes
  cb.check();
  cb.check();
  cb.check();
  // 2 succeed, 1 fails — majority success -> CLOSED
  cb.onSuccess();
  cb.onSuccess();
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.CLOSED, "Multi-probe: majority success -> CLOSED");
}

async function testCircuitBreakerMultiProbeAllFail(): Promise<void> {
  const cb = new CircuitBreaker("sno-gpu", "http://gpu:8080", { cooldownMs: 10, permittedHalfOpenCalls: 3 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  cb.check();
  cb.check();
  cb.check();
  // All 3 fail -> back to OPEN
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.OPEN, "Multi-probe: all failures -> back to OPEN");
}

async function testCircuitBreakerCancelProbeNoDoubleCount(): Promise<void> {
  const cb = new CircuitBreaker("sno-gpu", "http://gpu:8080", { cooldownMs: 10, permittedHalfOpenCalls: 3 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  cb.check();
  cb.check();
  cb.check();
  // Probe 1: onFailure then cancelProbe (simulates the pipeline flow)
  cb.onFailure(500);
  cb.cancelProbe(); // Should be no-op — probe already finalized
  // Probe 2 & 3: succeed
  cb.onSuccess();
  cb.onSuccess();
  // Without the fix, failure is double-counted -> 2 failures vs 2 successes -> OPEN
  // With the fix, 1 failure vs 2 successes -> CLOSED
  assertEqual(cb.state, CircuitState.CLOSED, "cancelProbe after onFailure must not double-count");
}

async function testCircuitBreakerCancelProbeDecrementsActive(): Promise<void> {
  const cb = new CircuitBreaker("sno-gpu", "http://gpu:8080", { cooldownMs: 10, permittedHalfOpenCalls: 2 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  cb.check(); // Probe 1
  cb.check(); // Probe 2 (slots full)
  // cancelProbe on probe 1 — counts as failure since not yet finalized
  cb.cancelProbe();
  // Probe 2 succeeds — now 1 fail + 1 success = complete, but tie -> OPEN
  cb.onSuccess();
  assertEqual(cb.state, CircuitState.OPEN, "cancelProbe counts as failure, tie -> OPEN");
}

async function testCircuitBreakerSingleProbeBackcompat(): Promise<void> {
  // permittedHalfOpenCalls=1 should behave like the old single-boolean
  const cb = new CircuitBreaker("openai", "https://api.openai.com", { cooldownMs: 10, permittedHalfOpenCalls: 1 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  assertEqual(cb.state, CircuitState.HALF_OPEN, "Enters HALF_OPEN");
  cb.check(); // Single probe allowed
  try {
    cb.check();
    assert(false, "Second probe should throw with permittedHalfOpenCalls=1");
  } catch (err) {
    assert(err instanceof CircuitOpenError, "Single-probe backcompat: second probe blocked");
  }
  cb.onSuccess();
  assertEqual(cb.state, CircuitState.CLOSED, "Single-probe backcompat: success -> CLOSED");
}

async function testCircuitBreakerMixedSuccessFailure(): Promise<void> {
  // 3 probes: 1 success, 2 failures -> majority failure -> OPEN
  const cb = new CircuitBreaker("sno-gpu", "http://gpu:8080", { cooldownMs: 10, permittedHalfOpenCalls: 3 });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  await new Promise((r) => setTimeout(r, 20));
  cb.check();
  cb.check();
  cb.check();
  cb.onSuccess();
  cb.onFailure(500);
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.OPEN, "Multi-probe: majority failure -> OPEN");
}

function testCircuitBreaker429Trips(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  cb.onFailure(429);
  cb.onFailure(429);
  cb.onFailure(429);
  assertEqual(cb.state, CircuitState.OPEN, "429 trips the breaker");
}

function testCircuitBreakerNetworkErrorTrips(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  cb.onFailure(undefined, true);
  cb.onFailure(undefined, true);
  cb.onFailure(undefined, true);
  assertEqual(cb.state, CircuitState.OPEN, "Network errors trip the breaker");
}

function testCircuitBreakerReset(): void {
  const cb = new CircuitBreaker("openai", "https://api.openai.com");
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.OPEN, "CB is OPEN");
  cb.reset();
  assertEqual(cb.state, CircuitState.CLOSED, "reset() -> CLOSED");
}

// ---------------------------------------------------------------------------
// Kill Switch Tests
// ---------------------------------------------------------------------------

function testKillSwitchNotActive(): void {
  const tmp = makeTmpDir();
  try {
    const ks = new KillSwitch(tmp);
    ks.check(); // Should not throw
    assert(true, "Kill switch check passes when file absent");
    assertEqual(ks.isActive(), false, "isActive() returns false when file absent");
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

function testKillSwitchActive(): void {
  const tmp = makeTmpDir();
  try {
    writeFileSync(join(tmp, "killswitch"), "");
    const ks = new KillSwitch(tmp);
    try {
      ks.check();
      assert(false, "Should have thrown KillSwitchActiveError");
    } catch (err) {
      assert(err instanceof KillSwitchActiveError, "Kill switch throws when file present");
    }
    assertEqual(ks.isActive(), true, "isActive() returns true when file present");
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

function testKillSwitchEnvResolution(): void {
  const tmp = makeTmpDir();
  const oldVal = process.env["LLMIX_STATE_DIR"];
  process.env["LLMIX_STATE_DIR"] = tmp;
  try {
    const ks = new KillSwitch();
    assert(ks.path.startsWith(tmp), "Kill switch resolves from LLMIX_STATE_DIR");
  } finally {
    if (oldVal === undefined) {
      delete process.env["LLMIX_STATE_DIR"];
    } else {
      process.env["LLMIX_STATE_DIR"] = oldVal;
    }
    rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Singleflight Tests
// ---------------------------------------------------------------------------

async function testSingleflightDedup(): Promise<void> {
  const sf = new Singleflight();
  let callCount = 0;

  async function expensive(): Promise<string> {
    callCount++;
    await new Promise((r) => setTimeout(r, 50));
    return "result";
  }

  const key = Singleflight.makeKey("test-request");
  const results = await Promise.all([
    sf.do(key, expensive),
    sf.do(key, expensive),
    sf.do(key, expensive),
  ]);

  assertEqual(callCount, 1, "Singleflight: fn called exactly once");
  assert(
    results.every((r) => r === "result"),
    "Singleflight: all waiters get same result",
  );
}

async function testSingleflightErrorPropagation(): Promise<void> {
  const sf = new Singleflight();

  async function failing(): Promise<string> {
    await new Promise((r) => setTimeout(r, 10));
    throw new Error("boom");
  }

  const key = Singleflight.makeKey("fail-request");
  const results = await Promise.allSettled([
    sf.do(key, failing),
    sf.do(key, failing),
  ]);

  assert(
    results.every((r) => r.status === "rejected"),
    "Singleflight: error propagated to all waiters",
  );
}

async function testSingleflightCleanup(): Promise<void> {
  const sf = new Singleflight();

  async function work(): Promise<number> {
    return 42;
  }

  await sf.do("test-key", work);
  assertEqual(sf.inFlightCount, 0, "Singleflight: map cleaned up after completion");
}

function testSingleflightMakeKey(): void {
  const key1 = Singleflight.makeKey("hello");
  const key2 = Singleflight.makeKey("hello");
  const key3 = Singleflight.makeKey("world");
  assertEqual(key1, key2, "Same input produces same key");
  assert(key1 !== key3, "Different input produces different key");
  assertEqual(key1.length, 64, "SHA-256 hex key is 64 chars");
}

// ---------------------------------------------------------------------------
// Retry Tests
// ---------------------------------------------------------------------------

function testCalculateDelayExponential(): void {
  const d0 = calculateDelay(0, { jitterMs: 0 });
  const d1 = calculateDelay(1, { jitterMs: 0 });
  const d2 = calculateDelay(2, { jitterMs: 0 });
  const d3 = calculateDelay(3, { jitterMs: 0 });
  assertEqual(d0, 1000, "attempt 0: 2^0 * 1000 = 1000");
  assertEqual(d1, 2000, "attempt 1: 2^1 * 1000 = 2000");
  assertEqual(d2, 4000, "attempt 2: 2^2 * 1000 = 4000");
  assertEqual(d3, 8000, "attempt 3: 2^3 * 1000 = 8000");
}

function testCalculateDelayCapped(): void {
  const d = calculateDelay(10, { jitterMs: 0 });
  assertEqual(d, 30000, "attempt 10: capped at 30000");
}

function testCalculateDelayWithJitter(): void {
  const d = calculateDelay(0);
  assert(d >= 1000 && d <= 2000, `attempt 0 with jitter: ${d} in [1000, 2000]`);
}

function testIsRetryable(): void {
  assertEqual(isRetryable(429), true, "429 is retryable");
  assertEqual(isRetryable(500), true, "500 is retryable");
  assertEqual(isRetryable(502), true, "502 is retryable");
  assertEqual(isRetryable(503), true, "503 is retryable");
  assertEqual(isRetryable(504), true, "504 is retryable");
  assertEqual(isRetryable(400), false, "400 is NOT retryable");
  assertEqual(isRetryable(401), false, "401 is NOT retryable");
  assertEqual(isRetryable(403), false, "403 is NOT retryable");
  assertEqual(isRetryable(404), false, "404 is NOT retryable");
  assertEqual(isRetryable(422), false, "422 is NOT retryable");
  assertEqual(isRetryable(200), false, "200 is NOT retryable");
}

function testParseRetryAfter(): void {
  assertEqual(parseRetryAfter("5"), 5000, "5 seconds -> 5000 ms");
  assertEqual(parseRetryAfter("0"), 0, "0 seconds -> 0 ms");
  assertEqual(parseRetryAfter("120"), 60000, "120 seconds capped at 60000 ms");
  assertEqual(parseRetryAfter(null), null, "null -> null");
  assertEqual(parseRetryAfter(undefined), null, "undefined -> null");
  assertEqual(parseRetryAfter("abc"), null, "Invalid -> null");
  assertEqual(parseRetryAfter("-1"), null, "Negative -> null");
}

function testRetryPolicyGetDelay(): void {
  const policy = new RetryPolicy({ maxRetries: 3 });
  // With Retry-After header
  const d = policy.getDelayMs(0, "10");
  assertEqual(d, 10000, "Retry-After takes precedence");

  // Without header, falls back to exponential
  const d2 = policy.getDelayMs(0, null);
  assert(d2 >= 1000 && d2 <= 2000, `Fallback to exponential: ${d2} in [1000, 2000]`);
}

async function testRetryPolicyExecute(): Promise<void> {
  let attemptCount = 0;
  const policy = new RetryPolicy({ maxRetries: 2, baseMs: 10, jitterMs: 0 });

  async function failingThenOk(): Promise<string> {
    attemptCount++;
    if (attemptCount < 3) throw new Error("transient");
    return "ok";
  }

  const result = await policy.execute(failingThenOk);
  assertEqual(result, "ok", "RetryPolicy: succeeds after retries");
  assertEqual(attemptCount, 3, "RetryPolicy: called 3 times (1 + 2 retries)");
}

async function testRetryPolicyNonRetryable(): Promise<void> {
  const policy = new RetryPolicy({ maxRetries: 3, baseMs: 10, jitterMs: 0 });
  let callCount = 0;

  async function alwaysFail(): Promise<string> {
    callCount++;
    throw new Error("permanent");
  }

  try {
    await policy.execute(alwaysFail, () => false);
    assert(false, "Should have thrown");
  } catch {
    assertEqual(callCount, 1, "Non-retryable: only called once");
  }
}

// ---------------------------------------------------------------------------
// New deterministic half-open tests (no setTimeout — direct field manipulation)
// ---------------------------------------------------------------------------

function testCircuitBreakerCooldownDoublesAndCaps(): void {
  // Bug: after HALF_OPEN -> OPEN re-failure, cooldown doubles.
  // If not capped, repeated oscillations leave the breaker OPEN indefinitely.
  // The cap is 300_000ms; ensure it never exceeds that.
  const cb = new CircuitBreaker("openai", "https://api.openai.com", {
    cooldownMs: 10_000,
    permittedHalfOpenCalls: 1,
  });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  assertEqual(cb.state, CircuitState.OPEN, "CB starts OPEN");
  const initialCooldown = cb.cooldownMs;

  // Expire the cooldown by backdating _openedAt past the cooldown window
  (cb as unknown as Record<string, unknown>)["_openedAt"] = performance.now() - initialCooldown - 1;
  assertEqual(cb.state, CircuitState.HALF_OPEN, "CB enters HALF_OPEN after expired cooldown");
  cb.check();
  cb.onFailure(500); // probe fails -> OPEN with doubled cooldown
  assertEqual(cb.state, CircuitState.OPEN, "HALF_OPEN -> OPEN after probe failure");
  assertEqual(cb.cooldownMs, initialCooldown * 2, "Cooldown doubled after re-open");

  // Drive to the cap: set cooldown to 200_000, fail -> should cap at 300_000
  cb.cooldownMs = 200_000;
  (cb as unknown as Record<string, unknown>)["_openedAt"] = performance.now() - 200_001;
  assertEqual(cb.state, CircuitState.HALF_OPEN, "CB enters HALF_OPEN again");
  cb.check();
  cb.onFailure(500);
  assertEqual(cb.cooldownMs, 300_000, "Cooldown capped at 300_000ms");
}

function testCircuitBreakerHalfOpenTieStaysOpen(): void {
  // Bug: with 2 permitted probes, 1-success + 1-failure is a tie.
  // The condition is `successes > failures` (strict greater-than), so
  // a tie means the circuit stays OPEN. Easy to mistake for ">=" semantics.
  const cb = new CircuitBreaker("openai", "https://api.openai.com", {
    cooldownMs: 10_000,
    permittedHalfOpenCalls: 2,
  });
  cb.onFailure(500);
  cb.onFailure(500);
  cb.onFailure(500);
  (cb as unknown as Record<string, unknown>)["_openedAt"] = performance.now() - 10_001;
  assertEqual(cb.state, CircuitState.HALF_OPEN, "CB in HALF_OPEN");

  cb.check(); // probe 1
  cb.check(); // probe 2
  cb.onSuccess(); // probe 1 succeeds
  cb.onFailure(500); // probe 2 fails

  // successes == failures == 1 -> tie -> OPEN (NOT CLOSED)
  assertEqual(cb.state, CircuitState.OPEN, "Tie (1S + 1F) resolves to OPEN, not CLOSED");
}

// ---------------------------------------------------------------------------
// Fixture-driven Tests
// ---------------------------------------------------------------------------

function testFromFixtures(): void {
  for (const code of scenarios.retryableStatusCodes) {
    assertEqual(isRetryable(code), true, `Fixture: ${code} is retryable`);
  }

  for (const code of scenarios.nonRetryableStatusCodes) {
    assertEqual(isRetryable(code), false, `Fixture: ${code} is NOT retryable`);
  }

  for (const entry of scenarios.retryDelayScenarios.attempts) {
    const d = calculateDelay(entry.attempt);
    assert(
      d >= entry.minMs && d <= entry.maxMs,
      `Fixture: attempt ${entry.attempt} delay ${d} in [${entry.minMs}, ${entry.maxMs}]`,
    );
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  testCircuitBreakerClosedToOpen();
  testCircuitBreakerAuthErrorsIgnored();
  testCircuitBreakerNonRetryableIgnored();
  testCircuitBreakerSuccessResets();
  testCircuitBreakerOpenBlocksCheck();
  await testCircuitBreakerHalfOpenToClosed();
  await testCircuitBreakerHalfOpenToOpen();
  await testCircuitBreakerHalfOpenBlocksSecondProbe();
  await testCircuitBreakerHalfOpenBlocksWhenFull();
  await testCircuitBreakerMultiProbeRecovery();
  await testCircuitBreakerMultiProbeAllFail();
  await testCircuitBreakerCancelProbeNoDoubleCount();
  await testCircuitBreakerCancelProbeDecrementsActive();
  await testCircuitBreakerSingleProbeBackcompat();
  await testCircuitBreakerMixedSuccessFailure();
  testCircuitBreaker429Trips();
  testCircuitBreakerNetworkErrorTrips();
  testCircuitBreakerReset();
  testKillSwitchNotActive();
  testKillSwitchActive();
  testKillSwitchEnvResolution();
  await testSingleflightDedup();
  await testSingleflightErrorPropagation();
  await testSingleflightCleanup();
  testSingleflightMakeKey();
  testCalculateDelayExponential();
  testCalculateDelayCapped();
  testCalculateDelayWithJitter();
  testIsRetryable();
  testParseRetryAfter();
  testRetryPolicyGetDelay();
  await testRetryPolicyExecute();
  await testRetryPolicyNonRetryable();
  testCircuitBreakerCooldownDoublesAndCaps();
  testCircuitBreakerHalfOpenTieStaysOpen();
  testFromFixtures();

  console.log(`\nResult: ${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
}

main();
