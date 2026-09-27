/**
 * OpenAI function tools through the real AI SDK.
 *
 * `kwargs.tools` in either OpenAI array shape must reach the provider as the same function tools.
 * Only the network endpoint is local; dispatch, the AI SDK and the OpenAI provider run for real,
 * so schema handling inside the AI SDK is exercised.
 */
import { openaiDispatch, snoGpuDispatch } from "../src/dispatchers.js";
import { InvalidToolsError } from "../src/types.js";

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
    if (new URL(request.url).pathname.endsWith("/responses")) {
      return Response.json({
        id: "resp_local",
        object: "response",
        created_at: 1790000000,
        status: "completed",
        model: "gpt-local",
        output: [
          {
            type: "message",
            id: "msg_local",
            role: "assistant",
            status: "completed",
            content: [{ type: "output_text", text: "ok", annotations: [] }],
          },
        ],
        usage: { input_tokens: 5, output_tokens: 1, total_tokens: 6 },
      });
    }
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

function dispatchWithTools(tools: unknown[]) {
  requestBody = undefined;
  return snoGpuDispatch()({
    provider: "sno-gpu",
    model: "local-model",
    apiKey: "local-key",
    messages: [{ role: "user", content: "Weather in Paris?" }],
    kwargs: { tools },
    config: { provider: "sno-gpu", model: "local-model" },
  });
}

function sentFunctions(): unknown {
  return (requestBody?.["tools"] as Array<Record<string, unknown>> | undefined)?.map((tool) => {
    const fn = tool["function"] as Record<string, unknown>;
    return { type: tool["type"], ...fn };
  });
}

const originalGpuBaseUrl = process.env["GPU_BASE_URL"];
process.env["GPU_BASE_URL"] = `http://127.0.0.1:${server.port}`;
try {
  const result = await dispatchWithTools([
    {
      type: "function",
      function: {
        name: "lookup_weather",
        description: "Look up the weather for a city.",
        parameters: weatherParameters,
      },
    },
  ]);
  assertDeepEq(
    sentFunctions(),
    [
      {
        type: "function",
        name: "lookup_weather",
        description: "Look up the weather for a city.",
        parameters: weatherParameters,
      },
    ],
    "nested OpenAI function tool reaches the provider with its name, description and parameters",
  );
  assertDeepEq(
    (result.toolCalls as Array<Record<string, unknown>> | undefined)?.map((call) => [call["toolName"], call["input"]]),
    [["lookup_weather", { city: "Paris" }]],
    "the model's call of that tool comes back to the caller",
  );

  await dispatchWithTools([{ type: "function", name: "lookup_weather", parameters: weatherParameters, strict: true }]);
  assertDeepEq(
    sentFunctions(),
    [{ type: "function", name: "lookup_weather", parameters: weatherParameters, strict: true }],
    "flat Responses-shape function tool reaches the provider with its strict flag",
  );

  let thrown: unknown;
  try {
    await dispatchWithTools([{ type: "function", function: { description: "no name" } }]);
  } catch (error) {
    thrown = error;
  }
  assertDeepEq(
    [thrown instanceof InvalidToolsError, thrown instanceof Error ? thrown.message : undefined, requestBody],
    [true, "kwargs.tools[0] is not an OpenAI function tool with a name", undefined],
    "a tool without a name fails as InvalidToolsError before any request is sent",
  );

  let duplicate: unknown;
  try {
    await dispatchWithTools([
      { type: "function", name: "lookup_weather", parameters: weatherParameters },
      { type: "function", function: { name: "lookup_weather", parameters: weatherParameters } },
    ]);
  } catch (error) {
    duplicate = error;
  }
  assertDeepEq(
    [duplicate instanceof InvalidToolsError, duplicate instanceof Error ? duplicate.message : undefined, requestBody],
    [true, 'kwargs.tools[1] repeats the tool name "lookup_weather"', undefined],
    "a repeated tool name fails as InvalidToolsError instead of silently dropping a tool",
  );

  requestBody = undefined;
  await openaiDispatch()({
    provider: "openai",
    model: "gpt-local",
    apiKey: "local-key",
    messages: [{ role: "user", content: "Weather in Paris?" }],
    kwargs: {
      baseUrl: `http://127.0.0.1:${server.port}/v1`,
      tools: [{ type: "function", function: { name: "lookup_weather", parameters: weatherParameters } }],
    },
    config: { provider: "openai", model: "gpt-local" },
  });
  assertDeepEq(
    (requestBody?.["tools"] as Array<Record<string, unknown>> | undefined)?.map((tool) => [tool["name"], tool["strict"]]),
    [["lookup_weather", true]],
    "the OpenAI provider always sends function tools in strict mode, like the Python OpenAI client",
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
