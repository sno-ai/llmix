/**
 * Tests for AIMD Adaptive Semaphore.
 *
 * Run with: bun test packages/llmix/typescript/tests/adaptive-semaphore.test.ts
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  AdaptiveSemaphore,
  parseOpenAIRatelimitHeaders,
} from "../src/adaptive-semaphore.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

interface Action {
  type: "success" | "rate_limit" | "header_feedback";
  remaining?: number;
  limit?: number;
}

interface Scenario {
  name: string;
  description: string;
  initial: number;
  actions: Action[];
  expected_window: number;
}

interface HeaderCase {
  name: string;
  headers: Record<string, string>;
  expected: { remaining: number; limit: number } | null;
}

interface Fixtures {
  defaults: { initial: number; min_concurrency: number; header_backoff_threshold: number };
  scenarios: Scenario[];
  header_parsing: HeaderCase[];
}

const fixturesPath = join(__dirname, "..", "..", "..", "..", "fixtures", "llmix", "aimd-scenarios.json");
const fixtures: Fixtures = JSON.parse(readFileSync(fixturesPath, "utf-8"));

function applyAction(sem: AdaptiveSemaphore, action: Action): void {
  switch (action.type) {
    case "success":
      sem.onSuccess();
      break;
    case "rate_limit":
      sem.onRateLimit();
      break;
    case "header_feedback":
      sem.onHeaderFeedback(action.remaining!, action.limit!);
      break;
  }
}

let passed = 0;
let failed = 0;

function assert(condition: boolean, msg: string): void {
  if (!condition) {
    console.log(`  FAIL: ${msg}`);
    failed++;
  } else {
    console.log(`  PASS: ${msg}`);
    passed++;
  }
}

// ---- Fixture-driven scenario tests ----

console.log("=== AIMD Adaptive Semaphore Tests ===\n");
console.log("Fixture-driven scenarios:");

for (const scenario of fixtures.scenarios) {
  const sem = new AdaptiveSemaphore(scenario.initial);
  for (const action of scenario.actions) {
    applyAction(sem, action);
  }
  assert(
    sem.window === scenario.expected_window,
    `${scenario.name} — window=${sem.window}, expected=${scenario.expected_window}`,
  );
}

// ---- Header parsing tests ----

console.log("\nHeader parsing:");

for (const tc of fixtures.header_parsing) {
  const result = parseOpenAIRatelimitHeaders(tc.headers);
  if (tc.expected === null) {
    assert(result === null, `header/${tc.name}`);
  } else {
    assert(
      result !== null &&
        result.remaining === tc.expected.remaining &&
        result.limit === tc.expected.limit,
      `header/${tc.name}`,
    );
  }
}

// ---- Additional unit tests ----

console.log("\nUnit tests:");

// Default parameters
{
  const sem = new AdaptiveSemaphore();
  assert(sem.window === 32, "default_window_32");
  assert(sem.maxConcurrency === 32, "default_max_32");
  assert(sem.minConcurrency === 4, "default_min_4");
}

// Custom min concurrency
{
  const sem = new AdaptiveSemaphore(16, 8);
  for (let i = 0; i < 10; i++) sem.onRateLimit();
  assert(sem.window === 8, "custom_min_concurrency_8");
}

// Header with zero limit is ignored
{
  const sem = new AdaptiveSemaphore(16);
  sem.onHeaderFeedback(100, 0);
  assert(sem.window === 16, "header_zero_limit_ignored");
}

// Acquire/release basic flow
{
  const sem = new AdaptiveSemaphore(2, 1);
  const p1 = sem.acquire();
  const p2 = sem.acquire();
  // Both should resolve immediately (2 permits)
  await p1;
  await p2;
  // Third should block
  let thirdResolved = false;
  const p3 = sem.acquire().then(() => {
    thirdResolved = true;
  });
  // Give microtask a chance
  await new Promise((r) => setTimeout(r, 10));
  assert(!thirdResolved, "acquire_blocks_when_full");
  sem.release();
  await p3;
  assert(thirdResolved, "release_unblocks_waiter");
  sem.release();
  sem.release();
}

// ---- Summary ----

console.log(`\n${"=".repeat(40)}`);
console.log(`Total: ${passed} passed, ${failed} failed`);

if (failed > 0) {
  process.exit(1);
}
console.log("\nAll tests passed!");
