// @ts-expect-error Bun's test module is available at runtime but not in this repo's TS check config.
import { mock } from "bun:test";

let passed = 0;
let failed = 0;
let createOpenAIOptions: Record<string, unknown> | undefined;
let createAnthropicOptions: Record<string, unknown> | undefined;
let fetchResponseMode: "default" | "reasoning-only" = "default";
let openRouterConstructorOptions: Record<string, unknown> | undefined;
let openRouterRequest: Record<string, unknown> | undefined;
let openRouterOptions: Record<string, unknown> | undefined;
const originalHeliconeApiKey = process.env["HELICONE_API_KEY"];
const originalHeliconeOpenaiBaseUrl = process.env["HELICONE_OPENAI_BASE_URL"];
const originalGpuBaseUrl = process.env["GPU_BASE_URL"];
delete process.env["HELICONE_API_KEY"];
delete process.env["HELICONE_OPENAI_BASE_URL"];

function assertEq<T>(actual: T, expected: T, msg: string): void {
  if (actual === expected) {
    passed++;
    console.log(`[PASS] ${msg}`);
  } else {
    failed++;
    console.log(`[FAIL] ${msg}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertDeepEq(actual: unknown, expected: unknown, msg: string): void {
  const actualJson = JSON.stringify(actual);
  const expectedJson = JSON.stringify(expected);
  if (actualJson === expectedJson) {
    passed++;
    console.log(`[PASS] ${msg}`);
  } else {
    failed++;
    console.log(`[FAIL] ${msg}: expected ${expectedJson}, got ${actualJson}`);
  }
}

function requireCreateOpenAIOptions(): Record<string, unknown> {
  if (!createOpenAIOptions) {
    throw new Error("createOpenAI was not called");
  }
  return createOpenAIOptions;
}

let capturedOptions: Record<string, unknown> | undefined;

function requireCapturedOptions(): Record<string, unknown> {
  if (!capturedOptions) {
    throw new Error("generateText was not called");
  }
  return capturedOptions;
}

mock.module("ai", () => ({
  Output: {
    text: () => ({ kind: "text" }),
    json: () => ({ kind: "json" }),
    object: (options: Record<string, unknown>) => ({ kind: "object", ...options }),
  },
  generateText: async (options: Record<string, unknown>) => {
    capturedOptions = options;
    return {
      text: "ok",
      finalStep: {
        response: { modelId: "gpt-4o-mini" },
        usage: {
          inputTokens: 1,
          outputTokens: 2,
          totalTokens: 3,
          inputTokenDetails: { cacheReadTokens: 1 },
        },
        toolCalls: [],
      },
    };
  },
}));

mock.module("@ai-sdk/openai", () => ({
  createOpenAI: (options: Record<string, unknown>) => {
    createOpenAIOptions = options;
    const provider = ((modelId: string) => ({ provider: "openai-responses", modelId })) as
      & ((modelId: string) => { provider: string; modelId: string })
      & { chat: (modelId: string) => { provider: string; modelId: string } };
    provider.chat = (modelId: string) => ({ provider: "openai-chat", modelId });
    return provider;
  },
}));

mock.module("@ai-sdk/anthropic", () => ({
  createAnthropic: (options: Record<string, unknown>) => {
    createAnthropicOptions = options;
    return (modelId: string) => ({ provider: "anthropic", modelId });
  },
}));

mock.module("@openrouter/sdk", () => ({
  OpenRouter: class {
    chat = {
      send: async (request: Record<string, unknown>, options: Record<string, unknown>) => {
        openRouterRequest = request;
        openRouterOptions = options;
        const chatRequest = request["chatRequest"] as Record<string, unknown>;
        return {
          id: "chatcmpl-test",
          object: "chat.completion",
          created: 1,
          choices: [
            {
              index: 0,
              finishReason: "stop",
              message: {
                role: "assistant",
                content: "42",
                toolCalls: [{ id: "call_1" }],
              },
            },
          ],
          model: chatRequest["model"] as string,
          usage: {
            promptTokens: 4,
            completionTokens: 2,
            totalTokens: 6,
            promptTokensDetails: { cachedTokens: 1 },
          },
          systemFingerprint: null,
        };
      },
    };

    constructor(options: Record<string, unknown>) {
      openRouterConstructorOptions = options;
    }
  },
}));

const { anthropicDispatch, openaiDispatch, openrouterDispatch, snoGpuDispatch } = await import("../src/dispatchers.js");

const dispatch = openaiDispatch();
const result = await dispatch({
  provider: "openai",
  model: "gpt-4o-mini",
  apiKey: "runtime-key",
  messages: [
    { role: "system", content: "Be concise." },
    { role: "user", content: "Say hello." },
  ],
  kwargs: {
    temperature: 0.2,
    top_p: 0.9,
    top_k: 42,
    presence_penalty: 0.3,
    frequency_penalty: 0.4,
    stop: ["END"],
    seed: 7,
    max_tokens: 256,
    response_format: { type: "json_object" },
  },
  config: {
    provider: "openai",
    model: "gpt-4o-mini",
  },
});

assertEq(result.content, "ok", "openai dispatch returns normalized content");
assertEq(result.model, "gpt-4o-mini", "openai dispatch returns final step model");
assertDeepEq(
  result.usage,
  { inputTokens: 1, outputTokens: 2, totalTokens: 3, cachedInputTokens: 1 },
  "openai dispatch returns final step usage",
);
assertEq(capturedOptions?.["instructions"], "Be concise.", "system message forwarded as instructions");
assertDeepEq(
  capturedOptions?.["messages"],
  [{ role: "user", content: "Say hello." }],
  "system message removed from AI SDK messages",
);
assertEq(
  (capturedOptions?.["model"] as { provider?: string } | undefined)?.provider,
  "openai-responses",
  "openai dispatch keeps default responses transport",
);
assertEq(capturedOptions?.["temperature"], 0.2, "temperature forwarded");
assertEq(capturedOptions?.["topP"], 0.9, "top_p forwarded as topP");
assertEq(capturedOptions?.["topK"], 42, "top_k forwarded as topK");
assertEq(capturedOptions?.["presencePenalty"], 0.3, "presence_penalty forwarded");
assertEq(capturedOptions?.["frequencyPenalty"], 0.4, "frequency_penalty forwarded");
assertDeepEq(capturedOptions?.["stopSequences"], ["END"], "stop forwarded as stopSequences");
assertEq(capturedOptions?.["seed"], 7, "seed forwarded");
assertEq(capturedOptions?.["maxOutputTokens"], 256, "max_tokens forwarded as maxOutputTokens");
assertDeepEq(capturedOptions?.["output"], { kind: "json" }, "json_object response_format forwarded via output");
assertEq(
  requireCreateOpenAIOptions()["baseURL"],
  "https://api.openai.com/v1",
  "openai dispatch uses OpenAI baseURL by default",
);
assertEq(
  requireCreateOpenAIOptions()["headers"],
  undefined,
  "openai dispatch does not send Helicone headers by default",
);

capturedOptions = undefined;
createOpenAIOptions = undefined;
process.env["HELICONE_API_KEY"] = "helicone-key";
process.env["HELICONE_OPENAI_BASE_URL"] = "https://helicone.internal.test/v1";

await dispatch({
  provider: "openai",
  model: "gpt-4o-mini",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: {},
  config: {
    provider: "openai",
    model: "gpt-4o-mini",
  },
});

assertEq(
  requireCreateOpenAIOptions()["baseURL"],
  "https://helicone.internal.test/v1",
  "openai dispatch routes through configured Helicone baseURL when HELICONE_API_KEY is set",
);
assertDeepEq(
  requireCreateOpenAIOptions()["headers"],
  { "Helicone-Auth": "Bearer helicone-key" },
  "openai dispatch sends Helicone auth header to configured Helicone baseURL",
);

createOpenAIOptions = undefined;
await dispatch({
  provider: "openai",
  model: "gpt-4o-mini",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: { baseUrl: "https://proxy.example.com/openai/custom/v9" },
  config: {
    provider: "openai",
    model: "gpt-4o-mini",
  },
});

assertEq(
  requireCreateOpenAIOptions()["baseURL"],
  "https://proxy.example.com/openai/custom/v9",
  "openai dispatch preserves explicit custom baseURL path",
);
assertEq(
  requireCreateOpenAIOptions()["headers"],
  undefined,
  "openai dispatch does not leak Helicone auth to non-Helicone baseURL",
);
if (originalHeliconeApiKey === undefined) {
  delete process.env["HELICONE_API_KEY"];
} else {
  process.env["HELICONE_API_KEY"] = originalHeliconeApiKey;
}
if (originalHeliconeOpenaiBaseUrl === undefined) {
  delete process.env["HELICONE_OPENAI_BASE_URL"];
} else {
  process.env["HELICONE_OPENAI_BASE_URL"] = originalHeliconeOpenaiBaseUrl;
}

const anthropic = anthropicDispatch();
await anthropic({
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: { baseUrl: "https://proxy.example.com/anthropic/custom/v9" },
  config: {
    provider: "anthropic",
    model: "claude-sonnet-4-5",
  },
});
assertEq(
  createAnthropicOptions?.["baseURL"],
  "https://proxy.example.com/anthropic/custom/v9",
  "anthropic dispatch preserves explicit custom baseURL path",
);

const openrouter = openrouterDispatch();
const openrouterResult = await openrouter({
  provider: "openrouter",
  model: "deepseek-v4-flash",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: {
    max_tokens: 32,
    extra_body: { provider: { sort: "price" } },
  },
  config: {
    provider: "openrouter",
    model: "deepseek-v4-flash",
  },
});

assertEq(openrouterResult.content, "42", "openrouter dispatch returns normalized content");
assertEq(openrouterResult.model, "deepseek/deepseek-v4-flash", "openrouter dispatch maps DeepSeek V4 Flash alias");
assertDeepEq(
  openrouterResult.usage,
  { inputTokens: 4, outputTokens: 2, totalTokens: 6, cachedInputTokens: 1 },
  "openrouter dispatch normalizes SDK usage",
);
assertEq(openRouterConstructorOptions?.["apiKey"], "runtime-key", "openrouter dispatch forwards API key");
assertEq(
  openRouterConstructorOptions?.["serverURL"],
  "https://openrouter.ai/api/v1",
  "openrouter dispatch configures OpenRouter serverURL",
);
assertEq(openRouterOptions?.["serverURL"], "https://openrouter.ai/api/v1", "openrouter dispatch sends serverURL option");
const openRouterChatRequest = openRouterRequest?.["chatRequest"] as Record<string, unknown> | undefined;
assertEq(openRouterChatRequest?.["model"], "deepseek/deepseek-v4-flash", "openrouter dispatch sends mapped model");
assertEq(openRouterChatRequest?.["maxTokens"], 32, "openrouter dispatch forwards max_tokens");
assertDeepEq(openRouterChatRequest?.["provider"], { sort: "price" }, "openrouter dispatch forwards provider routing");

await openrouter({
  provider: "openrouter",
  model: "deepseek-v4-flash",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: { baseUrl: "https://proxy.example.com/openrouter/custom/v9" },
  config: {
    provider: "openrouter",
    model: "deepseek-v4-flash",
  },
});
assertEq(
  openRouterConstructorOptions?.["serverURL"],
  "https://proxy.example.com/openrouter/custom/v9",
  "openrouter dispatch preserves explicit custom baseURL path",
);
assertEq(
  openRouterOptions?.["serverURL"],
  "https://proxy.example.com/openrouter/custom/v9",
  "openrouter dispatch sends explicit custom serverURL option",
);

await openrouter({
  provider: "openrouter",
  model: "deepseek-v4-flash",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: {
    extra_body: { provider: { sort: "price" } },
  },
  config: {
    provider: "openrouter",
    model: "deepseek-v4-flash",
    providerOptions: {
      openrouter: {
        provider: { sort: "latency" },
        reasoning: { enabled: false },
      },
    },
  },
});
const openRouterConfigRequest = openRouterRequest?.["chatRequest"] as Record<string, unknown> | undefined;
assertDeepEq(
  openRouterConfigRequest?.["provider"],
  { sort: "latency" },
  "openrouter dispatch lets config provider routing override default price sorting",
);
assertDeepEq(
  openRouterConfigRequest?.["reasoning"],
  { enabled: false },
  "openrouter dispatch forwards config reasoning",
);

await openrouter({
  provider: "openrouter",
  model: "qwen3.5-27b",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: {},
  config: {
    provider: "openrouter",
    model: "qwen3.5-27b",
  },
});
assertEq(
  (openRouterRequest?.["chatRequest"] as Record<string, unknown> | undefined)?.["model"],
  "qwen/qwen3.5-27b",
  "openrouter dispatch maps Qwen 3.5 27B alias",
);

await openrouter({
  provider: "openrouter",
  model: "qwen3.6-27b",
  apiKey: "runtime-key",
  messages: [{ role: "user", content: "Say hello." }],
  kwargs: {},
  config: {
    provider: "openrouter",
    model: "qwen3.6-27b",
  },
});
assertEq(
  (openRouterRequest?.["chatRequest"] as Record<string, unknown> | undefined)?.["model"],
  "qwen/qwen3.6-27b",
  "openrouter dispatch maps Qwen 3.6 27B alias",
);

const originalFetch = globalThis.fetch;
let forwardedBody: string | undefined;

globalThis.fetch = (async (_input, init) => {
  forwardedBody = typeof init?.body === "string" ? init.body : undefined;
  const payload = fetchResponseMode === "reasoning-only"
    ? {
        choices: [
          {
            message: {
              role: "assistant",
              content: "",
              reasoning_content: "Reasoning-only fallback answer",
            },
          },
        ],
      }
    : { ok: true };
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}) as typeof fetch;

try {
  const snoDispatch = snoGpuDispatch();
  const snoResult = await snoDispatch({
    provider: "sno-gpu",
    model: "deepseek-r1",
    apiKey: "runtime-key",
    messages: [{ role: "user", content: "Think this through." }],
    kwargs: {
      baseUrl: "https://gpu.example/v1",
      enable_thinking: true,
      thinking_budget: 512,
    },
    config: {
      provider: "sno-gpu",
      model: "deepseek-r1",
    },
  });

  assertEq(snoResult.content, "ok", "sno-gpu dispatch returns normalized content");
  assertEq(
    (requireCapturedOptions()["model"] as { provider?: string } | undefined)?.provider,
    "openai-chat",
    "sno-gpu dispatch uses chat transport for OpenAI-compatible gateway",
  );
  assertEq(
    requireCreateOpenAIOptions()["baseURL"],
    "https://gpu.example/v1",
    "sno-gpu dispatch forwards resolved baseURL",
  );
  assertDeepEq(
    requireCreateOpenAIOptions()["headers"],
    {
      "X-Internal-Token": "runtime-key",
      "X-Sno-LLM-Key": "runtime-key",
    },
    "sno-gpu dispatch forwards auth header",
  );
  const wrappedFetch = requireCreateOpenAIOptions()["fetch"];

  process.env["GPU_BASE_URL"] = "https://gpu.example";
  await snoDispatch({
    provider: "sno-gpu",
    model: "deepseek-r1",
    apiKey: "runtime-key",
    messages: [{ role: "user", content: "Extract this." }],
    kwargs: {},
    config: {
      provider: "sno-gpu",
      model: "deepseek-r1",
      providerOptions: {
        "sno-gpu": { gpuPath: "graph-extract" },
      },
    },
  });
  assertEq(
    requireCreateOpenAIOptions()["baseURL"],
    "https://gpu.example/graph-extract/v1",
    "sno-gpu dispatch allows graph-extract gpuPath",
  );

  await snoDispatch({
    provider: "sno-gpu",
    model: "deepseek-r1",
    apiKey: "runtime-key",
    messages: [{ role: "user", content: "Extract this." }],
    kwargs: {},
    config: {
      provider: "sno-gpu",
      model: "deepseek-r1",
      providerOptions: {
        "sno-gpu": { gpuPath: "future-safe-path" },
      },
    },
  });
  assertEq(
    requireCreateOpenAIOptions()["baseURL"],
    "https://gpu.example/future-safe-path/v1",
    "sno-gpu dispatch accepts unknown safe gpuPath",
  );

  let threwOnUnsafeGpuPath = false;
  try {
    await snoDispatch({
      provider: "sno-gpu",
      model: "deepseek-r1",
      apiKey: "runtime-key",
      messages: [{ role: "user", content: "Extract this." }],
      kwargs: {},
      config: {
        provider: "sno-gpu",
        model: "deepseek-r1",
        providerOptions: {
          "sno-gpu": { gpuPath: "../../etc/passwd" },
        },
      },
    });
  } catch (error) {
    threwOnUnsafeGpuPath = error instanceof Error &&
      error.message.includes("../../etc/passwd") &&
      error.message.includes("safe relative path");
  }
  assertEq(threwOnUnsafeGpuPath, true, "sno-gpu dispatch still rejects unsafe gpuPath");

  if (typeof wrappedFetch === "function") {
    passed++;
    console.log("[PASS] sno-gpu dispatch installs wrapped fetch");
    await wrappedFetch("https://gpu.example/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify({
        model: "deepseek-r1",
        messages: [{ role: "user", content: "hello" }],
        enable_thinking: true,
        thinking_budget: 512,
      }),
    });

    assertDeepEq(
      forwardedBody ? JSON.parse(forwardedBody) : undefined,
      {
        model: "deepseek-r1",
        messages: [{ role: "user", content: "hello" }],
        extra_body: {
          enable_thinking: true,
          thinking_budget: 512,
          chat_template_kwargs: {
            enable_thinking: true,
            thinking_budget: 512,
          },
        },
      },
      "sno-gpu wrapped fetch injects extra_body without requiring fetch.preconnect",
    );

    fetchResponseMode = "reasoning-only";
    const normalizedResponse = await wrappedFetch("https://gpu.example/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify({
        model: "deepseek-r1",
        messages: [{ role: "user", content: "hello" }],
      }),
    });
    fetchResponseMode = "default";

    assertDeepEq(
      await normalizedResponse.json(),
      {
        choices: [
          {
            message: {
              role: "assistant",
              content: "Reasoning-only fallback answer",
              reasoning_content: "Reasoning-only fallback answer",
            },
          },
        ],
      },
      "sno-gpu wrapped fetch mirrors reasoning_content into content when content is empty",
    );
  } else {
    failed++;
    console.log("[FAIL] sno-gpu dispatch installs wrapped fetch: fetch option missing");
  }
} finally {
  globalThis.fetch = originalFetch;
  if (originalGpuBaseUrl === undefined) {
    delete process.env["GPU_BASE_URL"];
  } else {
    process.env["GPU_BASE_URL"] = originalGpuBaseUrl;
  }
}

console.log(`\n${"=".repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
console.log("All tests passed!");
