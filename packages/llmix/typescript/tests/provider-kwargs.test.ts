/**
 * Provider kwargs injection tests.
 *
 * Tests each provider's kwargs transformation callback:
 * - OpenAI: reasoning model temperature/top_p stripping
 * - OpenRouter: extra_body.provider.sort injection
 * - Gemini: thinking_budget default
 * - Sno GPU: base URL construction
 * - No-op when callback is null/undefined
 */
import {
  applyTransformKwargs,
  geminiTransformKwargs,
  openaiTransformKwargs,
  openrouterTransformKwargs,
  snoGpuTransformKwargs,
  PROVIDER_KWARGS_REGISTRY,
  type TransformKwargsContext,
} from "../src/provider-kwargs.js"

let passed = 0
let failed = 0

function assert(condition: boolean, msg: string) {
  if (condition) {
    passed++
    console.log(`+ ${msg}`)
  } else {
    failed++
    console.log(`x ${msg}`)
  }
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

// =============================================================================
// No-op when callback is null/undefined
// =============================================================================

const baseCtx: TransformKwargsContext = { model: "gpt-4o", provider: "openai" }
const baseKwargs = { temperature: 0.7, top_p: 0.9 }

assert(
  applyTransformKwargs(baseCtx, baseKwargs, null) === baseKwargs,
  "null callback returns kwargs unchanged (same reference)",
)

assert(
  applyTransformKwargs(baseCtx, baseKwargs, undefined) === baseKwargs,
  "undefined callback returns kwargs unchanged (same reference)",
)

// =============================================================================
// OpenAI: reasoning model detection strips temperature and top_p
// =============================================================================

// Standard model -- no stripping
const openaiStdResult = openaiTransformKwargs(
  { model: "gpt-4o", provider: "openai" },
  { temperature: 0.5, top_p: 0.8, max_tokens: 100 },
)
assert(openaiStdResult["temperature"] === 0.5, "OpenAI standard model keeps temperature")
assert(openaiStdResult["top_p"] === 0.8, "OpenAI standard model keeps top_p")
assert(openaiStdResult["max_tokens"] === 100, "OpenAI standard model keeps max_tokens")

// o-series reasoning model -- strip temperature and top_p
const openaiO3Result = openaiTransformKwargs(
  { model: "o3-mini", provider: "openai" },
  { temperature: 0.5, top_p: 0.8, max_tokens: 100 },
)
assert(openaiO3Result["temperature"] === undefined, "OpenAI o3-mini strips temperature")
assert(openaiO3Result["top_p"] === undefined, "OpenAI o3-mini strips top_p")
assert(openaiO3Result["max_tokens"] === undefined, "OpenAI o3-mini removes max_tokens")
assert(openaiO3Result["max_completion_tokens"] === 100, "OpenAI o3-mini renames to max_completion_tokens")

// gpt-5 reasoning model -- strip
const openaiGpt5Result = openaiTransformKwargs(
  { model: "gpt-5", provider: "openai" },
  { temperature: 0.7, top_p: 0.9 },
)
assert(openaiGpt5Result["temperature"] === undefined, "OpenAI gpt-5 strips temperature")
assert(openaiGpt5Result["top_p"] === undefined, "OpenAI gpt-5 strips top_p")

// gpt-5-chat-latest IS a reasoning model — all gpt-5* variants strip temperature
const openaiGpt5ChatResult = openaiTransformKwargs(
  { model: "gpt-5-chat-latest", provider: "openai" },
  { temperature: 0.7, top_p: 0.9 },
)
assert(openaiGpt5ChatResult["temperature"] === undefined, "OpenAI gpt-5-chat-latest strips temperature (all gpt-5* are reasoning models)")
assert(openaiGpt5ChatResult["top_p"] === undefined, "OpenAI gpt-5-chat-latest strips top_p")

// o1 reasoning model
const openaiO1Result = openaiTransformKwargs(
  { model: "o1-preview", provider: "openai" },
  { temperature: 0.3 },
)
assert(openaiO1Result["temperature"] === undefined, "OpenAI o1-preview strips temperature")

// codex- reasoning model
const openaiCodexResult = openaiTransformKwargs(
  { model: "codex-mini", provider: "openai" },
  { temperature: 0.5, top_p: 0.7 },
)
assert(openaiCodexResult["temperature"] === undefined, "OpenAI codex-mini strips temperature")
assert(openaiCodexResult["top_p"] === undefined, "OpenAI codex-mini strips top_p")

// =============================================================================
// OpenRouter: inject extra_body.provider.sort = "price"
// =============================================================================

const orCtx: TransformKwargsContext = { model: "deepseek/deepseek-chat", provider: "deepseek" }

// No existing extra_body -- injects
const orResult1 = openrouterTransformKwargs(orCtx, { max_tokens: 100 })
assert(
  deepEqual((orResult1["extra_body"] as Record<string, unknown>)?.["provider"], { sort: "price" }),
  "OpenRouter injects provider.sort=price when no extra_body",
)
assert(orResult1["max_tokens"] === 100, "OpenRouter keeps other kwargs")

// Existing extra_body without provider -- injects
const orResult2 = openrouterTransformKwargs(orCtx, {
  extra_body: { custom: "value" },
})
const orBody2 = orResult2["extra_body"] as Record<string, unknown>
assert(
  deepEqual(orBody2["provider"], { sort: "price" }),
  "OpenRouter injects provider when extra_body exists without it",
)
assert(orBody2["custom"] === "value", "OpenRouter preserves existing extra_body keys")

// Existing extra_body with provider -- does NOT override
const orResult3 = openrouterTransformKwargs(orCtx, {
  extra_body: { provider: { sort: "latency" } },
})
assert(
  deepEqual((orResult3["extra_body"] as Record<string, unknown>)?.["provider"], { sort: "latency" }),
  "OpenRouter does NOT override existing provider config",
)

// providerOptions.openrouter supplies provider/reasoning before defaults
const orResult4 = openrouterTransformKwargs(
  {
    model: "deepseek/deepseek-chat",
    provider: "openrouter",
    providerOptions: { openrouter: { provider: { sort: "latency" }, reasoning: { enabled: false } } },
  },
  { extra_body: { custom: "value" } },
)
const orBody4 = orResult4["extra_body"] as Record<string, unknown>
assert(
  deepEqual(orBody4["provider"], { sort: "latency" }),
  "OpenRouter uses providerOptions.openrouter.provider before default routing",
)
assert(
  deepEqual(orBody4["reasoning"], { enabled: false }),
  "OpenRouter forwards providerOptions.openrouter.reasoning",
)
assert(orBody4["custom"] === "value", "OpenRouter keeps extra_body keys when applying providerOptions")

// =============================================================================
// Gemini: thinking_budget defaults to 0
// =============================================================================

const geminiCtx: TransformKwargsContext = { model: "gemini-2.5-pro", provider: "google" }

// No thinkingConfig -- sets default
const gemResult1 = geminiTransformKwargs(geminiCtx, { max_tokens: 100 })
assert(
  deepEqual(gemResult1["thinkingConfig"], { thinkingBudget: 0 }),
  "Gemini defaults thinkingBudget to 0",
)

// Override from providerOptions.google.thinkingBudget
const gemCtxBudget: TransformKwargsContext = {
  model: "gemini-2.5-pro",
  provider: "google",
  providerOptions: { google: { thinkingConfig: { thinkingBudget: 4096 } } },
}
const gemResult2 = geminiTransformKwargs(gemCtxBudget, {})
assert(
  deepEqual(gemResult2["thinkingConfig"], { thinkingBudget: 4096 }),
  "Gemini uses providerOptions.google.thinkingBudget override",
)

// Already set in kwargs -- preserves it
const gemResult3 = geminiTransformKwargs(geminiCtx, {
  thinkingConfig: { thinkingBudget: 2048 },
})
assert(
  deepEqual(gemResult3["thinkingConfig"], { thinkingBudget: 2048 }),
  "Gemini preserves existing thinkingBudget in kwargs",
)

// =============================================================================
// OpenAI: o2 IS a reasoning model in TypeScript (/^o\d/ divergence from Python)
// =============================================================================

// TypeScript uses /^o\d/.test(lower) which matches ANY o{digit} including o2, o5-o9.
// Python uses startswith(("o1","o3","o4",...)) which does NOT match o2.
// This means o2 gets temperature/top_p stripped in TypeScript but NOT in Python.
// These tests document the TypeScript side of the known behavioral divergence.

const openaiO2Result = openaiTransformKwargs(
  { model: "o2", provider: "openai" },
  { temperature: 0.5, top_p: 0.8 },
)
assert(
  openaiO2Result["temperature"] === undefined,
  "TypeScript: o2 IS a reasoning model (/^o\\d/ matches) — temperature stripped",
)
assert(
  openaiO2Result["top_p"] === undefined,
  "TypeScript: o2 IS a reasoning model — top_p stripped",
)

// Confirm o5 also matches (Python would NOT strip this)
const openaiO5Result = openaiTransformKwargs(
  { model: "o5-mini", provider: "openai" },
  { temperature: 0.3, max_tokens: 512 },
)
assert(
  openaiO5Result["temperature"] === undefined,
  "TypeScript: o5-mini IS a reasoning model — temperature stripped",
)
assert(
  openaiO5Result["max_completion_tokens"] === 512,
  "TypeScript: o5-mini renames max_tokens to max_completion_tokens",
)

// =============================================================================
// Gemini: explicit thinkingBudget=0 overrides enableThinking=true
// =============================================================================

// Bug: enableThinking=true with explicit thinkingBudget=0 in providerOptions must
// use the explicit budget (0), not skip injection. The `??` operator only skips
// null/undefined — 0 is not null so the explicit zero wins correctly.
// This test documents that the priority chain is explicit > enableThinking.

const gemCtxExplicitZero: TransformKwargsContext = {
  model: "gemini-2.5-pro",
  provider: "google",
  enableThinking: true,
  providerOptions: { google: { thinkingConfig: { thinkingBudget: 0 } } },
}
const gemResultExplicitZero = geminiTransformKwargs(gemCtxExplicitZero, {})
assert(
  deepEqual(gemResultExplicitZero["thinkingConfig"], { thinkingBudget: 0 }),
  "Gemini: explicit thinkingBudget=0 wins over enableThinking=true",
)

// Baseline: enableThinking=true without providerOptions leaves thinkingConfig absent
const gemCtxEnableOnly: TransformKwargsContext = {
  model: "gemini-2.5-pro",
  provider: "google",
  enableThinking: true,
}
const gemResultEnableOnly = geminiTransformKwargs(gemCtxEnableOnly, {})
assert(
  gemResultEnableOnly["thinkingConfig"] === undefined,
  "Gemini: enableThinking=true without explicit budget leaves thinkingConfig absent",
)

// =============================================================================
// Sno GPU: base URL construction
// =============================================================================

// With gpuPath
const gpuCtx = {
  model: "qwen3.6-27b-extract",
  provider: "sno-gpu",
  baseUrl: "https://gpu.example.com",
  providerOptions: { "sno-gpu": { gpuPath: "extract" } } as unknown as TransformKwargsContext["providerOptions"],
} as TransformKwargsContext
const gpuResult1 = snoGpuTransformKwargs(gpuCtx, {})
assert(
  gpuResult1["baseUrl"] === "https://gpu.example.com/extract/v1",
  "Sno GPU constructs URL with gpuPath",
)

// Without gpuPath -- fallback to /v1
const gpuCtxNoPath: TransformKwargsContext = {
  model: "qwen3.6-27b-reason",
  provider: "sno-gpu",
  baseUrl: "https://gpu.example.com",
}
const gpuResult2 = snoGpuTransformKwargs(gpuCtxNoPath, {})
assert(
  gpuResult2["baseUrl"] === "https://gpu.example.com/v1",
  "Sno GPU falls back to /v1 without gpuPath",
)

// Base URL already has /v1 -- strips before reconstructing
const gpuCtxWithV1 = {
  model: "qwen3.6-27b-reason",
  provider: "sno-gpu",
  baseUrl: "https://gpu.example.com/v1",
  providerOptions: { "sno-gpu": { gpuPath: "reason" } } as unknown as TransformKwargsContext["providerOptions"],
} as TransformKwargsContext
const gpuResult3 = snoGpuTransformKwargs(gpuCtxWithV1, {})
assert(
  gpuResult3["baseUrl"] === "https://gpu.example.com/reason/v1",
  "Sno GPU strips existing /v1 before reconstructing with gpuPath",
)

// Provider options propagate effective thinking controls
const gpuThinkingCtx: TransformKwargsContext = {
  model: "qwen3.6-27b-reason",
  provider: "sno-gpu",
  baseUrl: "https://gpu.example.com",
  providerOptions: {
    "sno-gpu": {
      enableThinking: true,
      thinkingBudget: 2048,
    },
  },
}
const gpuThinkingResult = snoGpuTransformKwargs(gpuThinkingCtx, {})
assert(
  gpuThinkingResult["enableThinking"] === true,
  "Sno GPU carries enableThinking from provider options",
)
assert(
  gpuThinkingResult["thinkingBudget"] === 2048,
  "Sno GPU carries thinkingBudget from provider options",
)

// enableThinking falls back to common config when provider options omit it
const gpuEnableFallback = snoGpuTransformKwargs(
  {
    model: "qwen3.6-27b-reason",
    provider: "sno-gpu",
    baseUrl: "https://gpu.example.com",
    enableThinking: true,
  },
  {},
)
assert(
  gpuEnableFallback["enableThinking"] === true,
  "Sno GPU falls back to common enableThinking when provider options omit it",
)

// Explicit kwargs still win over provider defaults
const gpuExplicitThinking = snoGpuTransformKwargs(gpuThinkingCtx, {
  enableThinking: false,
  thinkingBudget: 512,
})
assert(
  gpuExplicitThinking["enableThinking"] === false,
  "Sno GPU preserves explicit enableThinking in kwargs",
)
assert(
  gpuExplicitThinking["thinkingBudget"] === 512,
  "Sno GPU preserves explicit thinkingBudget in kwargs",
)

// Empty baseUrl -- throws
let threwOnEmpty = false
try {
  snoGpuTransformKwargs({ model: "qwen3.6-27b-reason", provider: "sno-gpu" }, {})
} catch (e) {
  threwOnEmpty = e instanceof Error && e.message.includes("non-empty base_url")
}
assert(threwOnEmpty, "Sno GPU throws on missing/empty baseUrl")

// =============================================================================
// Registry has expected entries
// =============================================================================

assert(PROVIDER_KWARGS_REGISTRY["openai"] === openaiTransformKwargs, "Registry has openai")
assert(PROVIDER_KWARGS_REGISTRY["openrouter"] === openrouterTransformKwargs, "Registry has openrouter -> openrouter")
assert(PROVIDER_KWARGS_REGISTRY["deepseek"] === openrouterTransformKwargs, "Registry has deepseek -> openrouter")
assert(PROVIDER_KWARGS_REGISTRY["google"] === geminiTransformKwargs, "Registry has google")
assert(PROVIDER_KWARGS_REGISTRY["sno-gpu"] === snoGpuTransformKwargs, "Registry has sno-gpu")

// =============================================================================
// Summary
// =============================================================================

console.log(`\n${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
