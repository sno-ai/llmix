/**
 * Integration tests for LLMix Call Pipeline.
 *
 * Tests the full 19-step call flow with a mock provider dispatch function.
 * Covers: happy path, error handling, singleflight dedup, semaphore release
 * on failure, circuit breaker behavior, thinking stripping, and key rotation.
 */

import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  CallPipeline,
  type ProviderResult,
  type DispatchContext,
  type PipelineConfig,
  type ProviderError,
} from "../src/pipeline.js";
import { KeyPool } from "../src/key-pool.js";
import { TwoTierCache } from "../src/response-cache.js";
import type { LLMConfig, LLMUsage } from "../src/types.js";

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

function assertArrayEqual<T>(actual: T[], expected: T[], msg: string): void {
  const matches = actual.length === expected.length && actual.every((value, index) => value === expected[index]);
  if (matches) {
    assert(true, msg);
  } else {
    assert(false, `${msg}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function makeConfig(overrides?: Partial<LLMConfig> & Record<string, unknown>): LLMConfig {
  return {
    provider: "openai",
    model: "gpt-4",
    common: { temperature: 0.7 },
    ...overrides,
  } as LLMConfig;
}

function makeUsage(): LLMUsage {
  return { inputTokens: 10, outputTokens: 20, totalTokens: 30 };
}

function mockDispatch(
  response: Partial<ProviderResult> = {},
): (ctx: DispatchContext) => Promise<ProviderResult> {
  return async (_ctx: DispatchContext): Promise<ProviderResult> => ({
    content: response.content ?? "Hello, world!",
    model: response.model ?? "gpt-4",
    usage: response.usage ?? makeUsage(),
    ...( response.headers != null ? { headers: response.headers } : {}),
  });
}

function makeErrorDispatch(
  statusCode?: number,
  message = "Provider error",
): (ctx: DispatchContext) => Promise<ProviderResult> {
  return async () => {
    const err = new Error(message) as ProviderError;
    if (statusCode !== undefined) {
      err.statusCode = statusCode;
    }
    throw err;
  };
}

function makePipelineConfig(
  dispatch: (ctx: DispatchContext) => Promise<ProviderResult>,
  overrides?: Partial<PipelineConfig>,
): PipelineConfig {
  return {
    dispatch,
    maxRetries: 0,
    retryBaseMs: 1,
    retryMaxDelayMs: 1,
    ...overrides,
  };
}

// =========================================================================
// Tests
// =========================================================================

async function testHappyPath(): Promise<void> {
  const pipeline = new CallPipeline(makePipelineConfig(mockDispatch()));
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));
  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "hi" }],
  });

  assertEqual(result.success, true, "happy path: success is true");
  assertEqual(result.content, "Hello, world!", "happy path: content matches");
  assertEqual(result.model, "gpt-4", "happy path: model matches");
  assertEqual(result.provider, "openai", "happy path: provider matches");
  assertEqual(result.usage.totalTokens, 30, "happy path: usage matches");
  assertEqual(result.error, undefined, "happy path: no error");
}

async function testErrorReturnsFailure(): Promise<void> {
  const pipeline = new CallPipeline(
    makePipelineConfig(makeErrorDispatch(500, "Server error")),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));
  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "hi" }],
  });

  assertEqual(result.success, false, "error: success is false");
  assert(result.error !== undefined, "error: error message present");
  assertEqual(result.content, "", "error: content is empty");
}

async function testThinkingStripping(): Promise<void> {
  const dispatch = mockDispatch({
    content: "<think>reasoning here</think>The answer is 42.",
  });
  const pipeline = new CallPipeline(makePipelineConfig(dispatch));
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));
  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "What?" }],
  });

  assertEqual(result.success, true, "thinking strip: success");
  assertEqual(result.content, "The answer is 42.", "thinking strip: content stripped");
  assertEqual(result.thinkingContent, "reasoning here", "thinking strip: thinking captured");
}

async function testKeepThinkingOutput(): Promise<void> {
  const dispatch = mockDispatch({
    content: "<think>reasoning</think>The answer.",
  });
  const pipeline = new CallPipeline(makePipelineConfig(dispatch));
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));
  const result = await pipeline.call({
    config: makeConfig({ common: { keepThinkingOutput: true } }),
    messages: [{ role: "user", content: "What?" }],
  });

  assertEqual(result.success, true, "keep thinking: success");
  assert(result.content.includes("<think>"), "keep thinking: thinking blocks preserved");
  assertEqual(result.thinkingContent, undefined, "keep thinking: no thinkingContent");
}

async function testSingleflightDedup(): Promise<void> {
  let callCount = 0;
  const dispatch = async (_ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    await new Promise((r) => setTimeout(r, 50));
    return { content: "ok", model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(makePipelineConfig(dispatch));
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));
  const config = makeConfig();
  const messages = [{ role: "user", content: "dedup" }];
  const sfKey = "dedup-key";

  const [r1, r2] = await Promise.all([
    pipeline.call({ config, messages, singleflightKey: sfKey }),
    pipeline.call({ config, messages, singleflightKey: sfKey }),
  ]);

  assertEqual(r1.success, true, "singleflight: first succeeds");
  assertEqual(r2.success, true, "singleflight: second succeeds");
  assertEqual(callCount, 1, "singleflight: dispatch called only once");
}

async function testSemaphoreReleaseOnFailure(): Promise<void> {
  const pipeline = new CallPipeline(
    makePipelineConfig(makeErrorDispatch(500)),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));
  // Make a failing call
  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "fail" }],
  });

  assertEqual(result.success, false, "semaphore release on failure: call fails");

  // If semaphore wasn't released, this would hang forever
  const pipeline2 = new CallPipeline(
    makePipelineConfig(mockDispatch(), { semaphoreInitial: 1, semaphoreMin: 1 }),
  );
  pipeline2.setKeyPool("openai", new KeyPool(["test-key"]));
  const r1 = await pipeline2.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "ok" }],
  });
  assertEqual(r1.success, true, "semaphore release on failure: subsequent call succeeds");
}

async function testCircuitBreakerTrips(): Promise<void> {
  let callCount = 0;
  const dispatch = async (_ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    const err = new Error("Server error") as ProviderError;
    err.statusCode = 500;
    throw err;
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      maxRetries: 0,
      circuitBreakerThreshold: 2,
      circuitBreakerCooldownMs: 60_000,
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const config = makeConfig();
  const messages = [{ role: "user", content: "trip" }];

  // First 2 calls trigger the breaker
  await pipeline.call({ config, messages, singleflightKey: "t1" });
  await pipeline.call({ config, messages, singleflightKey: "t2" });

  // Third call should be rejected by circuit breaker
  const result = await pipeline.call({ config, messages, singleflightKey: "t3" });
  assertEqual(result.success, false, "circuit breaker: third call fails");
  assert(result.error?.includes("Circuit breaker OPEN") === true, "circuit breaker: error message");

  // The breaker should have prevented the third dispatch
  assertEqual(callCount, 2, "circuit breaker: only 2 dispatches");
}

async function testCircuitBreakerOnlyCountsRetryable(): Promise<void> {
  let callCount = 0;
  const dispatch = async (_ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    const err = new Error("Bad request") as ProviderError;
    err.statusCode = 400; // Not retryable
    throw err;
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      maxRetries: 0,
      circuitBreakerThreshold: 2,
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const config = makeConfig();
  const messages = [{ role: "user", content: "400" }];

  // 400 errors should NOT trip the breaker
  await pipeline.call({ config, messages, singleflightKey: "a1" });
  await pipeline.call({ config, messages, singleflightKey: "a2" });
  await pipeline.call({ config, messages, singleflightKey: "a3" });

  assertEqual(callCount, 3, "non-retryable: all 3 dispatches executed (breaker not tripped)");
}

async function testKeyPoolRotation(): Promise<void> {
  const usedKeys: string[] = [];
  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    usedKeys.push(ctx.apiKey);
    if (usedKeys.length <= 1) {
      const err = new Error("Rate limited") as ProviderError;
      err.statusCode = 429;
      throw err;
    }
    return { content: "ok", model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, { maxRetries: 2, retryBaseMs: 1, retryMaxDelayMs: 1 }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["key-a", "key-b"]));

  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "rotate" }],
  });

  assertEqual(result.success, true, "key rotation: eventually succeeds");
  assertArrayEqual(usedKeys, ["key-a", "key-b"], "key rotation: retries advance to the next key");
}

async function testKillSwitch(): Promise<void> {
  const dir = join(tmpdir(), `llmix-ks-test-${Date.now()}`);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "killswitch"), "");

  const pipeline = new CallPipeline(
    makePipelineConfig(mockDispatch(), { killSwitchStateDir: dir }),
  );

  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "blocked" }],
  });

  assertEqual(result.success, false, "kill switch: call blocked");
  assert(result.error?.includes("Kill switch active") === true, "kill switch: error message");

  rmSync(dir, { recursive: true, force: true });
}

async function testRetryOnTransientError(): Promise<void> {
  let callCount = 0;
  const dispatch = async (_ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    if (callCount <= 2) {
      const err = new Error("Transient") as ProviderError;
      err.statusCode = 503;
      throw err;
    }
    return { content: "recovered", model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, { maxRetries: 3, retryBaseMs: 1, retryMaxDelayMs: 1 }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const result = await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "retry" }],
  });

  assertEqual(result.success, true, "retry: eventually succeeds");
  assertEqual(result.content, "recovered", "retry: correct content");
  assertEqual(callCount, 3, "retry: called 3 times (2 failures + 1 success)");
}

async function testHalfOpenCountsPerAdmittedExecution(): Promise<void> {
  const attempts = new Map<string, number>();
  let openingCall = true;

  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    const requestId = String((ctx.messages[0] as { content: string }).content);
    if (openingCall) {
      openingCall = false;
      const err = new Error("Open the breaker") as ProviderError;
      err.statusCode = 503;
      throw err;
    }

    const attempt = (attempts.get(requestId) ?? 0) + 1;
    attempts.set(requestId, attempt);
    if (attempt === 1) {
      const err = new Error("Transient") as ProviderError;
      err.statusCode = 503;
      throw err;
    }
    return { content: "recovered", model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      maxRetries: 1,
      retryBaseMs: 1,
      retryMaxDelayMs: 1,
      circuitBreakerThreshold: 1,
      circuitBreakerCooldownMs: 10,
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "open-breaker" }],
    singleflightKey: "open-breaker",
  });
  await new Promise((resolve) => setTimeout(resolve, 20));

  for (let i = 0; i < 5; i++) {
    const result = await pipeline.call({
      config: makeConfig(),
      messages: [{ role: "user", content: `recover-${i}` }],
      singleflightKey: `recover-${i}`,
    });
    assertEqual(result.success, true, `half-open retry accounting: recovery call ${i + 1} succeeds`);
  }

  assertEqual(
    pipeline.getCircuitBreakerState("openai", ""),
    "HALF_OPEN",
    "half-open retry accounting: breaker stays half-open until 10 admitted executions finish",
  );
}

async function testHalfOpenCountsFailedRetrySequenceOnce(): Promise<void> {
  const attempts = new Map<string, number>();
  let openingCall = true;

  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    const requestId = String((ctx.messages[0] as { content: string }).content);
    if (openingCall) {
      openingCall = false;
      const err = new Error("Open the breaker") as ProviderError;
      err.statusCode = 503;
      throw err;
    }

    const attempt = (attempts.get(requestId) ?? 0) + 1;
    attempts.set(requestId, attempt);
    const err = new Error("Still failing") as ProviderError;
    err.statusCode = 503;
    throw err;
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      maxRetries: 1,
      retryBaseMs: 1,
      retryMaxDelayMs: 1,
      circuitBreakerThreshold: 1,
      circuitBreakerCooldownMs: 10,
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  await pipeline.call({
    config: makeConfig(),
    messages: [{ role: "user", content: "open-breaker" }],
    singleflightKey: "open-breaker",
  });
  await new Promise((resolve) => setTimeout(resolve, 20));

  for (let i = 0; i < 5; i++) {
    const result = await pipeline.call({
      config: makeConfig(),
      messages: [{ role: "user", content: `still-failing-${i}` }],
      singleflightKey: `still-failing-${i}`,
    });
    assertEqual(result.success, false, `half-open failed retry accounting: call ${i + 1} fails`);
  }

  assertEqual(
    pipeline.getCircuitBreakerState("openai", ""),
    "HALF_OPEN",
    "half-open failed retry accounting: breaker still waits for 10 admitted executions",
  );
}

async function testCircuitBreakerScopedByEffectiveBaseUrl(): Promise<void> {
  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    const baseUrl = ctx.kwargs["baseUrl"];
    if (baseUrl === "https://bad.example/v1") {
      const err = new Error("Server error") as ProviderError;
      err.statusCode = 500;
      throw err;
    }
    return { content: String(baseUrl), model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      maxRetries: 0,
      circuitBreakerThreshold: 1,
      transformKwargsOverrides: {
        openai: (ctx, kwargs) => ({ ...kwargs, baseUrl: `${ctx.baseUrl}/v1` }),
      },
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const bad = await pipeline.call({
    config: makeConfig({ baseUrl: "https://bad.example" }),
    messages: [{ role: "user", content: "bad" }],
  });
  const good = await pipeline.call({
    config: makeConfig({ baseUrl: "https://good.example" }),
    messages: [{ role: "user", content: "good" }],
  });

  assertEqual(bad.success, false, "circuit breaker base URL: failing endpoint fails");
  assertEqual(good.success, true, "circuit breaker base URL: healthy endpoint remains callable");
  assertEqual(good.content, "https://good.example/v1", "circuit breaker base URL: dispatch uses transformed URL");
  assertEqual(
    pipeline.getCircuitBreakerState("openai", "https://bad.example/v1"),
    "OPEN",
    "circuit breaker base URL: failure opens only the bad endpoint breaker",
  );
  assertEqual(
    pipeline.getCircuitBreakerState("openai", "https://good.example/v1"),
    "CLOSED",
    "circuit breaker base URL: healthy endpoint uses a separate breaker",
  );
}

async function testSnoGpuPipelineAcceptsUnknownSafeGpuPath(): Promise<void> {
  let callCount = 0;
  let capturedContext: DispatchContext | undefined;
  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    capturedContext = ctx;
    return { content: String(ctx.kwargs["baseUrl"]), model: ctx.model, usage: makeUsage() };
  };

  const pipeline = new CallPipeline(makePipelineConfig(dispatch));
  pipeline.setKeyPool("sno-gpu", new KeyPool(["internal-token"]));
  const result = await pipeline.call({
    config: makeConfig({
      provider: "sno-gpu",
      model: "qwen3.6-27b-reason",
      baseUrl: "https://gpu.example.com",
      providerOptions: {
        "sno-gpu": { gpuPath: "future-safe-path" },
      },
    }),
    messages: [{ role: "user", content: "extract" }],
  });

  assertEqual(result.success, true, "sno-gpu custom path: pipeline succeeds");
  assertEqual(callCount, 1, "sno-gpu custom path: dispatch called once");
  assertEqual(
    capturedContext?.kwargs["baseUrl"],
    "https://gpu.example.com/future-safe-path/v1",
    "sno-gpu custom path: dispatch receives routed baseUrl",
  );
  assertEqual(capturedContext?.apiKey, "internal-token", "sno-gpu custom path: dispatch receives key");
}

async function testSnoGpuPipelineRejectsUnsafeGpuPathBeforeDispatch(): Promise<void> {
  let callCount = 0;
  const dispatch = async (_ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    return { content: "should not run", model: "qwen3.6-27b-reason", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(makePipelineConfig(dispatch));
  pipeline.setKeyPool("sno-gpu", new KeyPool(["internal-token"]));
  const result = await pipeline.call({
    config: makeConfig({
      provider: "sno-gpu",
      model: "qwen3.6-27b-reason",
      baseUrl: "https://gpu.example.com",
      providerOptions: {
        "sno-gpu": { gpuPath: "../escape" },
      },
    }),
    messages: [{ role: "user", content: "extract" }],
  });

  assertEqual(result.success, false, "sno-gpu unsafe path: pipeline returns failure");
  assertEqual(callCount, 0, "sno-gpu unsafe path: dispatch is not called");
  assert(
    result.error?.includes("safe relative path") ?? false,
    "sno-gpu unsafe path: error explains safe relative path requirement",
  );
}

async function testCacheKeyIncludesEffectiveBaseUrl(): Promise<void> {
  let callCount = 0;
  const cache = new TwoTierCache("memory", { maxItems: 8, ttlSeconds: 60 });
  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    return { content: String(ctx.kwargs["baseUrl"]), model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      responseCache: cache,
      transformKwargsOverrides: {
        openai: (ctx, kwargs) => ({ ...kwargs, baseUrl: `${ctx.baseUrl}/v1` }),
      },
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const messages = [{ role: "user", content: "cache" }];
  const a1 = await pipeline.call({
    config: makeConfig({ baseUrl: "https://a.example", caching: { strategy: "memory" } }),
    messages,
  });
  const b1 = await pipeline.call({
    config: makeConfig({ baseUrl: "https://b.example", caching: { strategy: "memory" } }),
    messages,
  });
  const a2 = await pipeline.call({
    config: makeConfig({ baseUrl: "https://a.example", caching: { strategy: "memory" } }),
    messages,
  });

  assertEqual(a1.success, true, "cache base URL: first endpoint succeeds");
  assertEqual(b1.success, true, "cache base URL: second endpoint succeeds");
  assertEqual(a2.success, true, "cache base URL: repeat endpoint succeeds");
  assertEqual(a1.content, "https://a.example/v1", "cache base URL: first endpoint response matches");
  assertEqual(b1.content, "https://b.example/v1", "cache base URL: second endpoint response matches");
  assertEqual(a2.cacheHit, "l1", "cache base URL: repeat endpoint hits its own cache entry");
  assertEqual(callCount, 2, "cache base URL: distinct endpoints do not share one cache key");

  await cache.close();
}

async function testSingleflightFallbackKeyIncludesBaseUrl(): Promise<void> {
  let callCount = 0;
  const dispatch = async (ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    await new Promise((resolve) => setTimeout(resolve, 50));
    return { content: String(ctx.kwargs["baseUrl"]), model: "gpt-4", usage: makeUsage() };
  };

  const pipeline = new CallPipeline(
    makePipelineConfig(dispatch, {
      transformKwargsOverrides: {
        openai: (ctx, kwargs) => ({ ...kwargs, baseUrl: ctx.baseUrl }),
      },
    }),
  );
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const messages = [{ role: "user", content: "same" }];
  const [r1, r2] = await Promise.all([
    pipeline.call({
      config: makeConfig({ baseUrl: "https://a.example" }),
      messages,
    }),
    pipeline.call({
      config: makeConfig({ baseUrl: "https://b.example" }),
      messages,
    }),
  ]);

  assertEqual(r1.success, true, "singleflight base URL: first call succeeds");
  assertEqual(r2.success, true, "singleflight base URL: second call succeeds");
  assertEqual(r1.content, "https://a.example", "singleflight base URL: first response kept separate");
  assertEqual(r2.content, "https://b.example", "singleflight base URL: second response kept separate");
  assertEqual(callCount, 2, "singleflight base URL: different endpoints do not deduplicate together");
}

async function testCacheSkippedWhenResponseHasToolCalls(): Promise<void> {
  // When provider returns toolCalls, pipeline must not write to cache because
  // CachedValue stores only text content, which would silently drop the
  // function-call structure on a subsequent hit. (GH #6)
  let callCount = 0;
  const cache = new TwoTierCache("memory", { maxItems: 8, ttlSeconds: 60 });
  const dispatch = async (_ctx: DispatchContext): Promise<ProviderResult> => {
    callCount++;
    return {
      content: "calling tool",
      model: "gpt-4",
      usage: makeUsage(),
      toolCalls: [{ id: "c1", type: "function", function: { name: "get_time", arguments: "{}" } }],
    };
  };

  const pipeline = new CallPipeline(makePipelineConfig(dispatch, { responseCache: cache }));
  pipeline.setKeyPool("openai", new KeyPool(["test-key"]));

  const config = makeConfig({ caching: { strategy: "memory" } });
  const messages = [{ role: "user", content: "what time?" }];

  const r1 = await pipeline.call({ config, messages });
  assertEqual(r1.success, true, "tool_calls skip-cache: first call succeeds");
  assertEqual(r1.toolCalls?.length ?? 0, 1, "tool_calls skip-cache: first call returns toolCalls");

  const r2 = await pipeline.call({ config, messages });
  assertEqual(r2.success, true, "tool_calls skip-cache: second call succeeds");
  assertEqual(callCount, 2, "tool_calls skip-cache: dispatch called twice (cache skipped)");
  assertEqual(r2.toolCalls?.length ?? 0, 1, "tool_calls skip-cache: second call still returns toolCalls");

  await cache.close();
}

// =========================================================================
// Runner
// =========================================================================

async function main(): Promise<void> {
  await testHappyPath();
  await testErrorReturnsFailure();
  await testThinkingStripping();
  await testKeepThinkingOutput();
  await testSingleflightDedup();
  await testSemaphoreReleaseOnFailure();
  await testCircuitBreakerTrips();
  await testCircuitBreakerOnlyCountsRetryable();
  await testKeyPoolRotation();
  await testKillSwitch();
  await testRetryOnTransientError();
  await testHalfOpenCountsPerAdmittedExecution();
  await testHalfOpenCountsFailedRetrySequenceOnce();
  await testCircuitBreakerScopedByEffectiveBaseUrl();
  await testSnoGpuPipelineAcceptsUnknownSafeGpuPath();
  await testSnoGpuPipelineRejectsUnsafeGpuPathBeforeDispatch();
  await testCacheKeyIncludesEffectiveBaseUrl();
  await testSingleflightFallbackKeyIncludesBaseUrl();
  await testCacheSkippedWhenResponseHasToolCalls();

  console.log(`\n${passed} passed, ${failed} failed, ${passed + failed} total`);
  if (failed > 0) {
    process.exit(1);
  }
}

await main();
