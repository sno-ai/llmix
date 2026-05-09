/**
 * Unit tests for KeyPool and loadKeysFromEnv.
 *
 * Run with: bun run tests/typescript/key-pool.test.ts
 */

import { KeyPool, KeyPoolExhaustedError, loadKeysFromEnv } from "../../typescript/src/key-pool.js";

let passed = 0;
let failed = 0;

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`Assertion failed: ${message}`);
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertArrayEqual<T>(actual: T[], expected: T[], label: string): void {
  if (actual.length !== expected.length || actual.some((v, i) => v !== expected[i])) {
    throw new Error(
      `${label}: expected [${expected.join(",")}], got [${actual.join(",")}]`,
    );
  }
}

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
    console.log(`+ ${name}`);
  } catch (e) {
    failed++;
    console.log(`x ${name}: ${e instanceof Error ? e.message : String(e)}`);
  }
}

console.log("Testing KeyPool:\n");

test("round_robin_order", () => {
  const pool = new KeyPool(["a", "b", "c"]);
  const results: string[] = [];
  for (let i = 0; i < 6; i++) {
    results.push(pool.select());
    pool.rotate();
  }
  assertArrayEqual(results, ["a", "b", "c", "a", "b", "c"], "round-robin");
});

test("429_rotation", () => {
  const pool = new KeyPool(["a", "b", "c"]);
  assertEqual(pool.select(), "a", "initial");
  pool.rotate(); // simulate 429
  assertEqual(pool.select(), "b", "after first rotate");
  pool.rotate(); // simulate another 429
  assertEqual(pool.select(), "c", "after second rotate");
});

test("select_does_not_advance_without_rotate", () => {
  const pool = new KeyPool(["a", "b", "c"]);
  assertEqual(pool.select(), "a", "first select");
  assertEqual(pool.select(), "a", "second select");
  pool.rotate();
  assertEqual(pool.select(), "b", "after rotate");
});

test("dead_key_skip", () => {
  const pool = new KeyPool(["a", "b", "c"]);
  pool.markDead("b");
  assertEqual(pool.select(), "a", "first select");
  pool.rotate();
  assertEqual(pool.select(), "c", "skip dead b");
  pool.rotate();
  assertEqual(pool.select(), "a", "wrap around");
});

test("all_exhausted_error", () => {
  const pool = new KeyPool(["a", "b"]);
  pool.markDead("a");
  pool.markDead("b");
  assert(pool.isExhausted(), "should be exhausted");
  try {
    pool.select();
    assert(false, "should have thrown");
  } catch (e) {
    assert(e instanceof KeyPoolExhaustedError, "should be KeyPoolExhaustedError");
  }
});

test("whitespace_trimming", () => {
  const pool = new KeyPool(["  a  ", " b ", "c  "]);
  assertEqual(pool.select(), "a", "first trimmed");
  pool.rotate();
  assertEqual(pool.select(), "b", "second trimmed");
  pool.rotate();
  assertEqual(pool.select(), "c", "third trimmed");
});

test("empty_key_filtering", () => {
  const pool = new KeyPool(["a", "", "  ", "b"]);
  assertEqual(pool.totalCount, 2, "count after filtering");
  assertEqual(pool.select(), "a", "first key");
  pool.rotate();
  assertEqual(pool.select(), "b", "second key");
});

test("all_empty_raises", () => {
  try {
    new KeyPool(["", "  ", ""]);
    assert(false, "should have thrown");
  } catch (e) {
    assert(e instanceof Error, "should be Error");
  }
});

test("empty_list_raises", () => {
  try {
    new KeyPool([]);
    assert(false, "should have thrown");
  } catch (e) {
    assert(e instanceof Error, "should be Error");
  }
});

test("alive_count", () => {
  const pool = new KeyPool(["a", "b", "c"]);
  assertEqual(pool.aliveCount, 3, "initial");
  pool.markDead("a");
  assertEqual(pool.aliveCount, 2, "after one dead");
  pool.markDead("b");
  assertEqual(pool.aliveCount, 1, "after two dead");
});

test("load_keys_from_env_multi", () => {
  process.env["TESTPROV_KEYS"] = "k1, k2, k3";
  try {
    const pool = loadKeysFromEnv("testprov");
    assertEqual(pool.totalCount, 3, "count");
    assertEqual(pool.select(), "k1", "first key");
    pool.rotate();
    assertEqual(pool.select(), "k2", "second key");
  } finally {
    delete process.env["TESTPROV_KEYS"];
  }
});

test("load_keys_from_env_single_fallback", () => {
  process.env["TESTPROV2_API_KEY"] = "single-key";
  delete process.env["TESTPROV2_KEYS"];
  try {
    const pool = loadKeysFromEnv("TESTPROV2");
    assertEqual(pool.totalCount, 1, "count");
    assertEqual(pool.select(), "single-key", "key value");
  } finally {
    delete process.env["TESTPROV2_API_KEY"];
  }
});

test("load_keys_from_env_missing_raises", () => {
  delete process.env["NOPROV_KEYS"];
  delete process.env["NOPROV_API_KEY"];
  try {
    loadKeysFromEnv("noprov");
    assert(false, "should have thrown");
  } catch (e) {
    assert(e instanceof Error, "should be Error");
    assert((e as Error).message.includes("NOPROV"), "should mention provider");
  }
});

test("load_keys_from_env_filters_empty", () => {
  process.env["TESTPROV3_KEYS"] = "k1,,  ,k2";
  try {
    const pool = loadKeysFromEnv("testprov3");
    assertEqual(pool.totalCount, 2, "count after filtering");
  } finally {
    delete process.env["TESTPROV3_KEYS"];
  }
});

console.log(`\nResults: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
