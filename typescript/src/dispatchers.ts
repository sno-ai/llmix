import type { JSONValue, SharedV3ProviderOptions as AiProviderOptions } from "@ai-sdk/provider";
import type { LanguageModel, ModelMessage, ToolSet } from "ai";
import {
  getAnthropicApiKey,
  getDeepinfraApiKey,
  getGeminiApiKey,
  getGpuBaseUrl,
  getNovitaApiKey,
  getOpenaiApiKey,
  getOpenrouterApiKey,
  getSnoLlmApiKey,
  getTogetherApiKey,
} from "./env";
import { lazyImport } from "./lazy-import";
import type { DispatchContext, ProviderDispatchFn, ProviderResult } from "./pipeline";
import type { LLMConfig, LLMUsage, ProviderOptions as LLMixProviderOptions } from "./types";

const OPENAI_BASE_URL = "https://api.openai.com/v1";
const ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1";
const GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1";
const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";
const DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai";
const NOVITA_BASE_URL = "https://api.novita.ai/v3/openai";
const TOGETHER_BASE_URL = "https://api.together.xyz/v1";
const VALID_GPU_PATHS = new Set(["extract", "reason"]);

const DEEPSEEK_MODEL_MAPPINGS: Record<string, string> = {
  "deepseek-chat": "deepseek/deepseek-chat-v3-0324",
  "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
  "deepseek-v3.2-speciale": "deepseek/deepseek-chat-v3-0324:free",
  "deepseek-reasoner": "deepseek/deepseek-reasoner",
};

// LH: Keep optional AI SDK peers lazy so `import "llmix"` does not fail
// for callers that only use neutral APIs such as config loading or pipeline setup.
const getAi = lazyImport<typeof import("ai")>("ai");
const getAnthropicSdk = lazyImport<typeof import("@ai-sdk/anthropic")>("@ai-sdk/anthropic");
const getGoogleSdk = lazyImport<typeof import("@ai-sdk/google")>("@ai-sdk/google");
const getOpenAiSdk = lazyImport<typeof import("@ai-sdk/openai")>("@ai-sdk/openai");

type TextConfig = { format: unknown };
type SnoGpuThinkingSettings = {
  enableThinking?: boolean;
  thinkingBudget?: number;
};
type ResponseFormatConfig =
  | { type: "text" }
  | { type: "json"; schema?: JSONValue; name?: string; description?: string };

function requireApiKey(
  apiKey: string | undefined,
  envValue: string | undefined,
  envName: string,
  provider: string,
): string {
  const resolved = apiKey?.trim() || envValue?.trim();
  if (resolved) {
    return resolved;
  }
  throw new Error(`${provider} provider requires ${envName}`);
}

function resolveBaseUrl(
  ctx: DispatchContext,
  fallback: string,
): string {
  const values = [
    ctx.kwargs["baseUrl"],
    (ctx.config as unknown as Record<string, unknown>)["baseUrl"],
    fallback,
  ];
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return fallback;
}

function buildGpuBaseUrl(gpuPath?: string): string {
  const baseUrl = getGpuBaseUrl()?.trim();
  if (!baseUrl) {
    throw new Error("sno-gpu provider requires GPU_BASE_URL");
  }
  if (!gpuPath) {
    return `${baseUrl}/v1`;
  }
  if (!VALID_GPU_PATHS.has(gpuPath)) {
    throw new Error(`Invalid gpu_path: ${JSON.stringify(gpuPath)}. Must be one of [\"extract\",\"reason\"]`);
  }
  return `${baseUrl}/${gpuPath}/v1`;
}

function resolveGpuBaseUrl(ctx: DispatchContext): string {
  const explicit = ctx.kwargs["baseUrl"];
  if (typeof explicit === "string" && explicit.trim()) {
    return explicit;
  }

  const configBaseUrl = (ctx.config as unknown as Record<string, unknown>)["baseUrl"];
  if (typeof configBaseUrl === "string" && configBaseUrl.trim()) {
    return configBaseUrl;
  }

  const providerOptions = (ctx.config.providerOptions?.["sno-gpu"] ?? {}) as Record<string, unknown>;
  const gpuPath = typeof providerOptions["gpuPath"] === "string" ? providerOptions["gpuPath"] : undefined;
  return buildGpuBaseUrl(gpuPath);
}

function resolveText(kwargs: Record<string, unknown>): TextConfig | undefined {
  const text = kwargs["text"];
  if (text && typeof text === "object" && "format" in text) {
    return text as TextConfig;
  }

  const responseFormat = kwargs["response_format"];
  if (typeof responseFormat === "string") {
    return { format: responseFormat };
  }
  if (responseFormat && typeof responseFormat === "object" && "type" in responseFormat) {
    return { format: (responseFormat as Record<string, unknown>)["type"] };
  }
  return undefined;
}

function resolveStringArray(value: unknown): string[] | undefined {
  if (typeof value === "string") {
    return [value];
  }
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value;
  }
  return undefined;
}

function resolveResponseFormat(kwargs: Record<string, unknown>): ResponseFormatConfig | undefined {
  const responseFormat = kwargs["response_format"];
  if (typeof responseFormat === "string") {
    if (responseFormat === "text") {
      return { type: "text" };
    }
    if (responseFormat === "json" || responseFormat === "json_object" || responseFormat === "json_schema") {
      return { type: "json" };
    }
    return undefined;
  }
  if (!isRecord(responseFormat)) {
    return undefined;
  }

  const type = responseFormat["type"];
  if (type === "text") {
    return { type: "text" };
  }
  if (type !== "json" && type !== "json_object" && type !== "json_schema") {
    return undefined;
  }

  const jsonSchema = isRecord(responseFormat["json_schema"]) ? responseFormat["json_schema"] : undefined;
  const schema = responseFormat["schema"] ?? jsonSchema?.["schema"];
  const name = typeof responseFormat["name"] === "string"
    ? responseFormat["name"]
    : typeof jsonSchema?.["name"] === "string"
      ? jsonSchema["name"]
      : undefined;
  const description = typeof responseFormat["description"] === "string"
    ? responseFormat["description"]
    : typeof jsonSchema?.["description"] === "string"
      ? jsonSchema["description"]
      : undefined;

  return {
    type: "json",
    ...(schema !== undefined ? { schema: schema as JSONValue } : {}),
    ...(name !== undefined ? { name } : {}),
    ...(description !== undefined ? { description } : {}),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function resolveTools(kwargs: Record<string, unknown>): ToolSet | undefined {
  const tools = kwargs["tools"];
  if (tools && typeof tools === "object") {
    return tools as ToolSet;
  }
  return undefined;
}

function resolveMaxOutputTokens(kwargs: Record<string, unknown>): number | undefined {
  for (const key of ["maxOutputTokens", "max_completion_tokens", "max_tokens"]) {
    const value = kwargs[key];
    if (typeof value === "number") {
      return value;
    }
  }
  return undefined;
}

function resolveProviderOptions(
  config: LLMConfig,
  key: keyof LLMixProviderOptions,
): AiProviderOptions | undefined {
  const value = config.providerOptions?.[key];
  if (!value) {
    return undefined;
  }
  return { [key]: value as Record<string, JSONValue | undefined> };
}

function extractUsage(usage: unknown): LLMUsage {
  const usageRecord = (usage ?? {}) as Record<string, unknown>;
  const inputTokens = typeof usageRecord["inputTokens"] === "number" ? usageRecord["inputTokens"] : 0;
  const outputTokens = typeof usageRecord["outputTokens"] === "number" ? usageRecord["outputTokens"] : 0;
  const totalTokens =
    typeof usageRecord["totalTokens"] === "number" ? usageRecord["totalTokens"] : inputTokens + outputTokens;
  const inputTokenDetails = usageRecord["inputTokenDetails"] as Record<string, unknown> | undefined;
  const cachedInputTokens =
    inputTokenDetails && typeof inputTokenDetails["cacheReadTokens"] === "number"
      ? inputTokenDetails["cacheReadTokens"]
      : undefined;
  return { inputTokens, outputTokens, totalTokens, cachedInputTokens };
}

function normalizeResult(
  content: string,
  model: string,
  usage: unknown,
  toolCalls: unknown[] | undefined,
): ProviderResult {
  return {
    content,
    model,
    usage: extractUsage(usage),
    toolCalls,
  };
}

function mapDeepseekModel(model: string): string {
  if (model.startsWith("deepseek/")) {
    return model;
  }
  return DEEPSEEK_MODEL_MAPPINGS[model] ?? `deepseek/${model}`;
}

function resolveBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function resolveNumber(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function resolveSnoGpuThinking(ctx: DispatchContext): SnoGpuThinkingSettings {
  const snoGpuOptions = ctx.config.providerOptions?.["sno-gpu"];
  const enableThinking =
    resolveBoolean(ctx.kwargs["enableThinking"]) ??
    resolveBoolean(ctx.kwargs["enable_thinking"]) ??
    snoGpuOptions?.enableThinking ??
    ctx.config.common?.enableThinking;
  const thinkingBudget =
    resolveNumber(ctx.kwargs["thinkingBudget"]) ??
    resolveNumber(ctx.kwargs["thinking_budget"]) ??
    snoGpuOptions?.thinkingBudget;

  return {
    ...(enableThinking !== undefined ? { enableThinking } : {}),
    ...(thinkingBudget !== undefined ? { thinkingBudget } : {}),
  };
}

function injectSnoGpuExtraBody(
  payload: Record<string, unknown>,
  thinking: SnoGpuThinkingSettings,
): Record<string, unknown> {
  if (thinking.enableThinking === undefined && thinking.thinkingBudget === undefined) {
    return payload;
  }

  const result = { ...payload };
  delete result["enableThinking"];
  delete result["enable_thinking"];
  delete result["thinkingBudget"];
  delete result["thinking_budget"];

  const rawExtraBody = result["extra_body"];
  const extraBody = isRecord(rawExtraBody) ? { ...rawExtraBody } : {};
  const rawChatTemplateKwargs = extraBody["chat_template_kwargs"];
  const chatTemplateKwargs = isRecord(rawChatTemplateKwargs) ? { ...rawChatTemplateKwargs } : {};

  // LH: sno-gpu reads thinking controls from extra_body for OpenAI-compatible requests.
  if (thinking.enableThinking !== undefined) {
    extraBody["enable_thinking"] = thinking.enableThinking;
    chatTemplateKwargs["enable_thinking"] = thinking.enableThinking;
  }
  if (thinking.thinkingBudget !== undefined) {
    extraBody["thinking_budget"] = thinking.thinkingBudget;
    chatTemplateKwargs["thinking_budget"] = thinking.thinkingBudget;
  }
  if (Object.keys(chatTemplateKwargs).length > 0) {
    extraBody["chat_template_kwargs"] = chatTemplateKwargs;
  }
  result["extra_body"] = extraBody;
  return result;
}

function createSnoGpuFetch(ctx: DispatchContext): typeof fetch {
  const thinking = resolveSnoGpuThinking(ctx);

  const impl = async (
    input: Parameters<typeof fetch>[0],
    init?: Parameters<typeof fetch>[1],
  ): Promise<Response> => {
    if (typeof init?.body !== "string") {
      return fetch(input, init);
    }

    let payload: unknown;
    try {
      payload = JSON.parse(init.body);
    } catch {
      return fetch(input, init);
    }

    if (!isRecord(payload)) {
      return fetch(input, init);
    }

    const nextPayload = injectSnoGpuExtraBody(payload, thinking);
    if (nextPayload === payload) {
      return fetch(input, init);
    }

    return fetch(input, {
      ...init,
      body: JSON.stringify(nextPayload),
    });
  };

  // Bun's `typeof fetch` includes a `preconnect` method; forward it from the
  // global so the wrapper is assignable wherever the SDK expects full fetch.
  return Object.assign(impl, { preconnect: fetch.preconnect.bind(fetch) });
}

async function generateWithModel(
  ctx: DispatchContext,
  model: LanguageModel,
  providerOptions?: AiProviderOptions,
): Promise<ProviderResult> {
  const { generateText, Output } = await getAi();
  const messages = ctx.messages as ModelMessage[];
  const temperature = typeof ctx.kwargs["temperature"] === "number" ? ctx.kwargs["temperature"] : undefined;
  const seed = typeof ctx.kwargs["seed"] === "number" ? ctx.kwargs["seed"] : undefined;
  const maxOutputTokens = resolveMaxOutputTokens(ctx.kwargs);
  const text = resolveText(ctx.kwargs);
  const responseFormat = resolveResponseFormat(ctx.kwargs);
  const output =
    text?.format === "text"
      ? Output.text()
      : responseFormat?.type === "json" && responseFormat.schema !== undefined
        ? Output.object({
            schema: responseFormat.schema as never,
            ...(responseFormat.name !== undefined ? { name: responseFormat.name } : {}),
            ...(responseFormat.description !== undefined ? { description: responseFormat.description } : {}),
          })
        : responseFormat?.type === "json"
          ? Output.json({
              ...(responseFormat.name !== undefined ? { name: responseFormat.name } : {}),
              ...(responseFormat.description !== undefined ? { description: responseFormat.description } : {}),
            })
          : undefined;
  const tools = resolveTools(ctx.kwargs);
  const topP = typeof ctx.kwargs["top_p"] === "number" ? ctx.kwargs["top_p"] : undefined;
  const topK = typeof ctx.kwargs["top_k"] === "number" ? ctx.kwargs["top_k"] : undefined;
  const presencePenalty =
    typeof ctx.kwargs["presence_penalty"] === "number" ? ctx.kwargs["presence_penalty"] : undefined;
  const frequencyPenalty =
    typeof ctx.kwargs["frequency_penalty"] === "number" ? ctx.kwargs["frequency_penalty"] : undefined;
  const stopSequences = resolveStringArray(ctx.kwargs["stop"]);

  const result = await generateText({
    model,
    messages,
    ...(temperature !== undefined ? { temperature } : {}),
    ...(topP !== undefined ? { topP } : {}),
    ...(topK !== undefined ? { topK } : {}),
    ...(presencePenalty !== undefined ? { presencePenalty } : {}),
    ...(frequencyPenalty !== undefined ? { frequencyPenalty } : {}),
    ...(stopSequences ? { stopSequences } : {}),
    ...(seed !== undefined ? { seed } : {}),
    ...(maxOutputTokens !== undefined ? { maxOutputTokens } : {}),
    ...(output ? { output } : {}),
    ...(tools ? { tools } : {}),
    ...(providerOptions ? { providerOptions } : {}),
  });
  return normalizeResult(result.text, result.response.modelId ?? ctx.model, result.usage, result.toolCalls as unknown[] | undefined);
}

export function openaiDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const openai = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getOpenaiApiKey(), "OPENAI_API_KEY", "openai"),
      baseURL: resolveBaseUrl(ctx, OPENAI_BASE_URL),
    });
    return generateWithModel(ctx, openai(ctx.model), resolveProviderOptions(ctx.config, "openai"));
  };
}

export function anthropicDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createAnthropic } = await getAnthropicSdk();
    const anthropic = createAnthropic({
      apiKey: requireApiKey(ctx.apiKey, getAnthropicApiKey(), "ANTHROPIC_API_KEY", "anthropic"),
      baseURL: resolveBaseUrl(ctx, ANTHROPIC_BASE_URL),
    });
    return generateWithModel(ctx, anthropic(ctx.model), resolveProviderOptions(ctx.config, "anthropic"));
  };
}

export function geminiDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createGoogleGenerativeAI } = await getGoogleSdk();
    const google = createGoogleGenerativeAI({
      apiKey: requireApiKey(ctx.apiKey, getGeminiApiKey(), "GEMINI_API_KEY", "google"),
      baseURL: resolveBaseUrl(ctx, GOOGLE_BASE_URL),
    });
    return generateWithModel(ctx, google(ctx.model), resolveProviderOptions(ctx.config, "google"));
  };
}

// OpenRouter is OpenAI-compatible — no dedicated provider package needed.
// Reuses @ai-sdk/openai with baseURL=https://openrouter.ai/api/v1.
// Ref: https://openrouter.ai/docs/quickstart
export function openrouterDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const openrouter = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getOpenrouterApiKey(), "OPENROUTER_API_KEY", "deepseek"),
      baseURL: resolveBaseUrl(ctx, OPENROUTER_BASE_URL),
    });
    return generateWithModel(ctx, openrouter(mapDeepseekModel(ctx.model)), resolveProviderOptions(ctx.config, "deepseek"));
  };
}

export function deepinfraDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const deepinfra = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getDeepinfraApiKey(), "DEEPINFRA_API_KEY", "deepinfra"),
      baseURL: resolveBaseUrl(ctx, DEEPINFRA_BASE_URL),
    });
    return generateWithModel(ctx, deepinfra(ctx.model), resolveProviderOptions(ctx.config, "deepinfra"));
  };
}

export function novitaDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const novita = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getNovitaApiKey(), "NOVITA_API_KEY", "novita"),
      baseURL: resolveBaseUrl(ctx, NOVITA_BASE_URL),
    });
    return generateWithModel(ctx, novita(ctx.model), resolveProviderOptions(ctx.config, "novita"));
  };
}

export function togetherDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const together = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getTogetherApiKey(), "TOGETHER_API_KEY", "together"),
      baseURL: resolveBaseUrl(ctx, TOGETHER_BASE_URL),
    });
    return generateWithModel(ctx, together(ctx.model), resolveProviderOptions(ctx.config, "together"));
  };
}

export function snoGpuDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const openai = createOpenAI({
      apiKey: "not-used",
      baseURL: resolveGpuBaseUrl(ctx),
      fetch: createSnoGpuFetch(ctx),
      headers: {
        "X-Sno-LLM-Key": requireApiKey(ctx.apiKey, getSnoLlmApiKey(), "SNO_LLM_API_KEY", "sno-gpu"),
      },
    });
    return generateWithModel(ctx, openai(ctx.model), undefined);
  };
}
