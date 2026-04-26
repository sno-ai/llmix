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

const OPENROUTER_MODEL_MAPPINGS: Record<string, string> = {
  "deepseek-chat": "deepseek/deepseek-chat-v3-0324",
  "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
  "deepseek-v3.2-speciale": "deepseek/deepseek-chat-v3-0324:free",
  "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
  "deepseek-reasoner": "deepseek/deepseek-reasoner",
  "qwen3.5-27b": "qwen/qwen3.5-27b",
  "qwen3.6-27b": "qwen/qwen3.6-27b",
};

// LH: Keep optional AI SDK peers lazy so `import "llmix"` does not fail
// for callers that only use neutral APIs such as config loading or pipeline setup.
const getAi = lazyImport<typeof import("ai")>("ai");
const getAnthropicSdk = lazyImport<typeof import("@ai-sdk/anthropic")>("@ai-sdk/anthropic");
const getGoogleSdk = lazyImport<typeof import("@ai-sdk/google")>("@ai-sdk/google");
const getOpenAiSdk = lazyImport<typeof import("@ai-sdk/openai")>("@ai-sdk/openai");
const getOpenRouterSdk = lazyImport<typeof import("@openrouter/sdk")>("@openrouter/sdk");

type TextConfig = { format: unknown };
type SnoGpuThinkingSettings = {
  enableThinking?: boolean;
  thinkingBudget?: number;
};
type FetchWithOptionalPreconnect = typeof fetch & {
  preconnect?: (...args: unknown[]) => unknown;
};
type OpenAiCompatibleProvider = ((modelId: string) => LanguageModel) & {
  chat: (modelId: string) => LanguageModel;
};
type ResponseFormatConfig =
  | { type: "text" }
  | { type: "json"; schema?: JSONValue; name?: string; description?: string };
type OpenRouterMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content?: unknown;
  toolCalls?: unknown;
  toolCallId?: string;
  name?: string;
};
type OpenRouterChatRequest = Record<string, unknown> & {
  model: string;
  messages: OpenRouterMessage[];
  stream: false;
};
type OpenRouterChatResult = {
  choices?: Array<{
    message?: {
      content?: unknown;
      reasoning?: unknown;
      reasoningContent?: unknown;
      toolCalls?: unknown[];
      tool_calls?: unknown[];
    };
  }>;
  model?: string;
  usage?: unknown;
};

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

function mapOpenRouterModel(model: string): string {
  if (model.includes("/")) {
    return model;
  }
  const mapped = OPENROUTER_MODEL_MAPPINGS[model];
  if (mapped !== undefined) {
    return mapped;
  }
  if (model.startsWith("deepseek")) {
    return `deepseek/${model}`;
  }
  if (model.startsWith("qwen")) {
    return `qwen/${model}`;
  }
  return model;
}

function resolveOpenAiCompatibleChatModel(
  provider: OpenAiCompatibleProvider,
  model: string,
): LanguageModel {
  // LH: OpenAI-compatible gateways such as sno-gpu, Together, Novita,
  // DeepInfra, and OpenRouter commonly expose chat/completions but not
  // OpenAI's newer /responses API. Force the chat transport for those
  // providers while preserving the default OpenAI behavior elsewhere.
  return provider.chat(model);
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

function normalizeSnoGpuResponsePayload(payload: unknown): unknown {
  if (!isRecord(payload) || !Array.isArray(payload["choices"])) {
    return payload;
  }

  let changed = false;
  const choices = payload["choices"].map((choice) => {
    if (!isRecord(choice) || !isRecord(choice["message"])) {
      return choice;
    }

    const message = choice["message"];
    const content = message["content"];
    const reasoningContent = message["reasoning_content"];
    if (
      typeof reasoningContent !== "string" ||
      !reasoningContent.trim() ||
      !((typeof content === "string" && content.length === 0) || content === null)
    ) {
      return choice;
    }

    changed = true;
    return {
      ...choice,
      message: {
        ...message,
        // LH: Some sno-gpu chat-completions deployments emit answer text only
        // through `reasoning_content`. Mirror it into `content` so OpenAI-
        // compatible SDK consumers receive usable text.
        content: reasoningContent,
      },
    };
  });

  if (!changed) {
    return payload;
  }

  return {
    ...payload,
    choices,
  };
}

function createSnoGpuFetch(ctx: DispatchContext): typeof fetch {
  const thinking = resolveSnoGpuThinking(ctx);

  const impl = async (
    input: Parameters<typeof fetch>[0],
    init?: Parameters<typeof fetch>[1],
  ): Promise<Response> => {
    const nextInit = (() => {
      if (typeof init?.body !== "string") {
        return init;
      }

      let payload: unknown;
      try {
        payload = JSON.parse(init.body);
      } catch {
        return init;
      }

      if (!isRecord(payload)) {
        return init;
      }

      const nextPayload = injectSnoGpuExtraBody(payload, thinking);
      if (nextPayload === payload) {
        return init;
      }

      return {
        ...init,
        body: JSON.stringify(nextPayload),
      };
    })();

    const response = await fetch(input, nextInit);
    const contentType = response.headers.get("content-type");
    if (!contentType?.toLowerCase().includes("application/json")) {
      return response;
    }

    let responsePayload: unknown;
    try {
      responsePayload = await response.clone().json();
    } catch {
      return response;
    }

    const normalizedPayload = normalizeSnoGpuResponsePayload(responsePayload);
    if (normalizedPayload === responsePayload) {
      return response;
    }

    return new Response(JSON.stringify(normalizedPayload), {
      status: response.status,
      statusText: response.statusText,
      headers: new Headers(response.headers),
    });
  };

  const preconnect = (fetch as FetchWithOptionalPreconnect).preconnect;
  if (typeof preconnect === "function") {
    // Preserve Bun's fetch.preconnect when available without assuming it
    // exists in standard runtimes or DOM/Node fetch typings.
    return Object.assign(impl, { preconnect: preconnect.bind(fetch) }) as typeof fetch;
  }

  return impl as typeof fetch;
}

function toOpenRouterMessages(messages: ModelMessage[]): OpenRouterMessage[] {
  const result: OpenRouterMessage[] = [];
  for (const message of messages) {
    if (!isRecord(message)) {
      continue;
    }
    const record = message as Record<string, unknown>;
    const role = record["role"];
    if (role !== "system" && role !== "user" && role !== "assistant" && role !== "tool") {
      continue;
    }

    const next: OpenRouterMessage = { role };
    if (record["content"] !== undefined) {
      next.content = record["content"];
    } else if (role !== "assistant" || record["toolCalls"] === undefined) {
      next.content = "";
    }
    if (record["toolCalls"] !== undefined) {
      next.toolCalls = record["toolCalls"];
    }
    if (typeof record["toolCallId"] === "string") {
      next.toolCallId = record["toolCallId"];
    }
    if (typeof record["name"] === "string") {
      next.name = record["name"];
    }
    result.push(next);
  }
  return result;
}

function setOpenRouterNumber(
  request: OpenRouterChatRequest,
  kwargs: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = kwargs[sourceKey];
  if (typeof value === "number") {
    request[targetKey] = value;
  }
}

function setOpenRouterBoolean(
  request: OpenRouterChatRequest,
  kwargs: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = kwargs[sourceKey];
  if (typeof value === "boolean") {
    request[targetKey] = value;
  }
}

function setOpenRouterValue(
  request: OpenRouterChatRequest,
  kwargs: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = kwargs[sourceKey];
  if (value !== undefined) {
    request[targetKey] = value;
  }
}

function resolveOpenRouterRecord(
  ctx: DispatchContext,
  key: "provider" | "reasoning",
): Record<string, unknown> | undefined {
  const direct = ctx.kwargs[key];
  if (isRecord(direct)) {
    return direct;
  }

  const extraBody = ctx.kwargs["extra_body"];
  const extraValue = isRecord(extraBody) && isRecord(extraBody[key]) ? extraBody[key] : undefined;

  const openrouterOptions = ctx.config.providerOptions?.openrouter;
  const optionValue = openrouterOptions?.[key];
  if (
    optionValue !== undefined &&
    (extraValue === undefined || (key === "provider" && isOpenRouterDefaultProvider(extraValue)))
  ) {
    return optionValue;
  }
  if (extraValue !== undefined) {
    return extraValue;
  }
  return undefined;
}

function isOpenRouterDefaultProvider(value: Record<string, unknown>): boolean {
  return Object.keys(value).length === 1 && value["sort"] === "price";
}

function buildOpenRouterChatRequest(ctx: DispatchContext, model: string): OpenRouterChatRequest {
  const request: OpenRouterChatRequest = {
    model,
    messages: toOpenRouterMessages(ctx.messages as ModelMessage[]),
    stream: false,
  };

  setOpenRouterNumber(request, ctx.kwargs, "temperature", "temperature");
  setOpenRouterNumber(request, ctx.kwargs, "top_p", "topP");
  setOpenRouterNumber(request, ctx.kwargs, "seed", "seed");
  setOpenRouterNumber(request, ctx.kwargs, "presence_penalty", "presencePenalty");
  setOpenRouterNumber(request, ctx.kwargs, "frequency_penalty", "frequencyPenalty");
  setOpenRouterBoolean(request, ctx.kwargs, "parallel_tool_calls", "parallelToolCalls");
  setOpenRouterValue(request, ctx.kwargs, "stop", "stop");
  setOpenRouterValue(request, ctx.kwargs, "tools", "tools");
  setOpenRouterValue(request, ctx.kwargs, "tool_choice", "toolChoice");
  setOpenRouterValue(request, ctx.kwargs, "response_format", "responseFormat");
  setOpenRouterValue(request, ctx.kwargs, "service_tier", "serviceTier");
  setOpenRouterValue(request, ctx.kwargs, "session_id", "sessionId");
  setOpenRouterValue(request, ctx.kwargs, "plugins", "plugins");
  setOpenRouterValue(request, ctx.kwargs, "models", "models");

  const maxCompletionTokens = ctx.kwargs["max_completion_tokens"];
  if (typeof maxCompletionTokens === "number") {
    request["maxCompletionTokens"] = maxCompletionTokens;
  } else {
    const maxTokens = resolveMaxOutputTokens(ctx.kwargs);
    if (maxTokens !== undefined) {
      request["maxTokens"] = maxTokens;
    }
  }

  const provider = resolveOpenRouterRecord(ctx, "provider");
  if (provider !== undefined) {
    request["provider"] = provider;
  }
  const reasoning = resolveOpenRouterRecord(ctx, "reasoning");
  if (reasoning !== undefined) {
    request["reasoning"] = reasoning;
  }

  return request;
}

function extractOpenRouterContent(message: unknown): string {
  if (!isRecord(message)) {
    return "";
  }
  const content = message["content"];
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    const textParts = content.flatMap((part) => {
      if (typeof part === "string") {
        return [part];
      }
      if (isRecord(part) && typeof part["text"] === "string") {
        return [part["text"]];
      }
      return [];
    });
    if (textParts.length > 0) {
      return textParts.join("\n");
    }
  }
  const reasoning = message["reasoning"] ?? message["reasoningContent"];
  return typeof reasoning === "string" ? reasoning : "";
}

function extractOpenRouterUsage(usage: unknown): LLMUsage {
  if (!isRecord(usage)) {
    return { inputTokens: 0, outputTokens: 0, totalTokens: 0 };
  }
  const inputTokens = typeof usage["promptTokens"] === "number" ? usage["promptTokens"] : 0;
  const outputTokens = typeof usage["completionTokens"] === "number" ? usage["completionTokens"] : 0;
  const totalTokens =
    typeof usage["totalTokens"] === "number" ? usage["totalTokens"] : inputTokens + outputTokens;
  const promptTokensDetails = isRecord(usage["promptTokensDetails"]) ? usage["promptTokensDetails"] : undefined;
  const cachedInputTokens =
    promptTokensDetails && typeof promptTokensDetails["cachedTokens"] === "number"
      ? promptTokensDetails["cachedTokens"]
      : undefined;
  return { inputTokens, outputTokens, totalTokens, cachedInputTokens };
}

function normalizeOpenRouterResult(result: OpenRouterChatResult, fallbackModel: string): ProviderResult {
  const message = result.choices?.[0]?.message;
  const toolCalls = message?.toolCalls ?? message?.tool_calls;
  return {
    content: extractOpenRouterContent(message),
    model: result.model ?? fallbackModel,
    usage: extractOpenRouterUsage(result.usage),
    toolCalls,
  };
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

export function openrouterDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { OpenRouter } = await getOpenRouterSdk();
    const serverURL = resolveBaseUrl(ctx, OPENROUTER_BASE_URL);
    const openrouter = new OpenRouter({
      apiKey: requireApiKey(ctx.apiKey, getOpenrouterApiKey(), "OPENROUTER_API_KEY", "openrouter"),
      serverURL,
    });
    const model = mapOpenRouterModel(ctx.model);
    const chatRequest = buildOpenRouterChatRequest(ctx, model);
    const result = await openrouter.chat.send({ chatRequest: chatRequest as never }, { serverURL });
    return normalizeOpenRouterResult(result as OpenRouterChatResult, model);
  };
}

export function deepinfraDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const deepinfra = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getDeepinfraApiKey(), "DEEPINFRA_API_KEY", "deepinfra"),
      baseURL: resolveBaseUrl(ctx, DEEPINFRA_BASE_URL),
    });
    return generateWithModel(
      ctx,
      resolveOpenAiCompatibleChatModel(deepinfra as OpenAiCompatibleProvider, ctx.model),
      resolveProviderOptions(ctx.config, "deepinfra"),
    );
  };
}

export function novitaDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const novita = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getNovitaApiKey(), "NOVITA_API_KEY", "novita"),
      baseURL: resolveBaseUrl(ctx, NOVITA_BASE_URL),
    });
    return generateWithModel(
      ctx,
      resolveOpenAiCompatibleChatModel(novita as OpenAiCompatibleProvider, ctx.model),
      resolveProviderOptions(ctx.config, "novita"),
    );
  };
}

export function togetherDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const together = createOpenAI({
      apiKey: requireApiKey(ctx.apiKey, getTogetherApiKey(), "TOGETHER_API_KEY", "together"),
      baseURL: resolveBaseUrl(ctx, TOGETHER_BASE_URL),
    });
    return generateWithModel(
      ctx,
      resolveOpenAiCompatibleChatModel(together as OpenAiCompatibleProvider, ctx.model),
      resolveProviderOptions(ctx.config, "together"),
    );
  };
}

export function snoGpuDispatch(): ProviderDispatchFn {
  return async (ctx) => {
    const { createOpenAI } = await getOpenAiSdk();
    const apiKey = requireApiKey(
      ctx.apiKey,
      getSnoLlmApiKey(),
      "SNO_LLM_API_KEY or INTERNAL_SERVICE_SECRET",
      "sno-gpu",
    );
    const openai = createOpenAI({
      apiKey: "not-used",
      baseURL: resolveGpuBaseUrl(ctx),
      fetch: createSnoGpuFetch(ctx),
      headers: {
        // LH: Current sno-gpu gateways require X-Internal-Token, but keep the
        // legacy header for older deployments that still expect it.
        "X-Internal-Token": apiKey,
        "X-Sno-LLM-Key": apiKey,
      },
    });
    return generateWithModel(
      ctx,
      resolveOpenAiCompatibleChatModel(openai as OpenAiCompatibleProvider, ctx.model),
      undefined,
    );
  };
}
