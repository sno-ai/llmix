/**
 * OpenAI function tools through the real AI SDK.
 *
 * `kwargs.tools` in the OpenAI array shape must reach the provider as the same function tools.
 * Only the network endpoint is local; dispatch, the AI SDK and the OpenAI provider run for real,
 * so schema handling inside the AI SDK is exercised.
 */
import { snoGpuDispatch } from "../src/dispatchers.js";

let passed = 0;
let failed = 0;

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

const weatherParameters = {
  type: "object",
  properties: { city: { type: "string" } },
  required: ["city"],
  additionalProperties: false,
};

let requestBody: Record<string, unknown> | undefined;
// @ts-expect-error Bun globals are available at runtime but not in this repo's TS check config.
const server = Bun.serve({
  port: 0,
  async fetch(request: Request): Promise<Response> {
    requestBody = (await request.json()) as Record<string, unknown>;
    return Response.json({
      id: "chatcmpl-local",
      object: "chat.completion",
      created: 1790000000,
      model: "local-model",
      choices: [
        {
          index: 0,
          finish_reason: "tool_calls",
          message: {
            role: "assistant",
            content: null,
            tool_calls: [
              {
                id: "call_1",
                type: "function",
                function: { name: "lookup_weather", arguments: '{"city":"Paris"}' },
              },
            ],
          },
        },
      ],
      usage: { prompt_tokens: 12, completion_tokens: 7, total_tokens: 19 },
    });
  },
});

const originalGpuBaseUrl = process.env["GPU_BASE_URL"];
process.env["GPU_BASE_URL"] = `http://127.0.0.1:${server.port}`;
try {
  const result = await snoGpuDispatch()({
    provider: "sno-gpu",
    model: "local-model",
    apiKey: "local-key",
    messages: [{ role: "user", content: "Weather in Paris?" }],
    kwargs: {
      tools: [
        {
          type: "function",
          function: {
            name: "lookup_weather",
            description: "Look up the weather for a city.",
            parameters: weatherParameters,
          },
        },
      ],
    },
    config: { provider: "sno-gpu", model: "local-model" },
  });

  const sentTools = (requestBody?.["tools"] as Array<Record<string, unknown>> | undefined)?.map((tool) => {
    const fn = tool["function"] as Record<string, unknown>;
    return { type: tool["type"], name: fn["name"], description: fn["description"], parameters: fn["parameters"] };
  });
  assertDeepEq(
    sentTools,
    [
      {
        type: "function",
        name: "lookup_weather",
        description: "Look up the weather for a city.",
        parameters: weatherParameters,
      },
    ],
    "OpenAI function tool reaches the provider with its name, description and parameters",
  );
  assertDeepEq(
    (result.toolCalls as Array<Record<string, unknown>> | undefined)?.map((call) => [call["toolName"], call["input"]]),
    [["lookup_weather", { city: "Paris" }]],
    "the model's call of that tool comes back to the caller",
  );
} catch (error) {
  failed++;
  console.log(`[FAIL] dispatch with OpenAI function tools threw: ${String(error)}`);
} finally {
  server.stop(true);
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
