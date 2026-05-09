/**
 * Tests for HTTP/2 transport configuration.
 *
 * Run with: bun run tests/typescript/http2.test.ts
 *
 * Verifies provider registry flags and factory stubs without making
 * actual network calls.
 */

import {
  PROVIDER_TRANSPORT,
  type ProviderTransportConfig,
  createOpenAITransport,
  getProviderTransport,
} from "../../typescript/src/http2.js";

let passed = 0;
let failed = 0;

function assertEq<T>(actual: T, expected: T, msg: string): void {
  if (actual === expected) {
    passed++;
    console.log(`[PASS] ${msg}`);
  } else {
    failed++;
    console.log(`[FAIL] ${msg}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertTrue(condition: boolean, msg: string): void {
  if (condition) {
    passed++;
    console.log(`[PASS] ${msg}`);
  } else {
    failed++;
    console.log(`[FAIL] ${msg}`);
  }
}

// ---- Provider registry flags ----

function testOpenAIHttp2() {
  const cfg = getProviderTransport("openai");
  assertEq(cfg.http2, true, "OpenAI should declare HTTP/2 intent");
  assertEq(cfg.name, "openai", "OpenAI name matches");
}

function testGeminiHttp1InTS() {
  const cfg = getProviderTransport("gemini");
  assertEq(cfg.http2, false, "Gemini should be HTTP/1.1 in TypeScript (SDK limitation)");
}

function testProxyProvidersHttp1() {
  for (const provider of ["openrouter", "helicone"]) {
    const cfg = getProviderTransport(provider);
    assertEq(cfg.http2, false, `${provider} should use HTTP/1.1`);
  }
}

function testAnthropicHttp1() {
  const cfg = getProviderTransport("anthropic");
  assertEq(cfg.http2, false, "Anthropic should use HTTP/1.1");
}

function testDeepSeekHttp1() {
  const cfg = getProviderTransport("deepseek");
  assertEq(cfg.http2, false, "DeepSeek should use HTTP/1.1");
}

function testUnknownProviderDefaultsHttp1() {
  const cfg = getProviderTransport("some-new-provider");
  assertEq(cfg.http2, false, "Unknown provider defaults to HTTP/1.1");
  assertEq(cfg.name, "some-new-provider", "Unknown provider preserves name");
}

function testRegistryCompleteness() {
  const expected = new Set(["openai", "anthropic", "gemini", "deepseek", "openrouter", "helicone"]);
  const actual = new Set(Object.keys(PROVIDER_TRANSPORT));
  assertEq(actual.size, expected.size, "Registry has correct number of providers");
  for (const p of expected) {
    assertTrue(actual.has(p), `Registry contains ${p}`);
  }
}

// ---- Cross-language consistency ----

function testPythonTSGeminiDifference() {
  // In Python, Gemini is http2=true (httpx handles it).
  // In TypeScript, Gemini is http2=false (SDK limitation).
  // This test documents the intentional divergence.
  const cfg = getProviderTransport("gemini");
  assertEq(cfg.http2, false, "TS Gemini is intentionally HTTP/1.1 (see Python for HTTP/2)");
}

// ---- OpenAI transport stub ----

function testCreateOpenAITransportStub() {
  const result = createOpenAITransport();
  assertEq(result, undefined, "OpenAI transport stub returns undefined (not yet implemented)");
}

// ---- Type verification ----

function testProviderTransportConfigShape() {
  // Verify the shape at runtime matches the interface
  const cfg: ProviderTransportConfig = PROVIDER_TRANSPORT["openai"]!;
  assertTrue(typeof cfg.name === "string", "name is string");
  assertTrue(typeof cfg.http2 === "boolean", "http2 is boolean");
}

// ---- Run all ----

testOpenAIHttp2();
testGeminiHttp1InTS();
testProxyProvidersHttp1();
testAnthropicHttp1();
testDeepSeekHttp1();
testUnknownProviderDefaultsHttp1();
testRegistryCompleteness();
testPythonTSGeminiDifference();
testCreateOpenAITransportStub();
testProviderTransportConfigShape();

console.log(`\n${"=".repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
console.log("All tests passed!");
