/**
 * Centralized environment variable access for package/llmix.
 * All process.env reads for the LLMix TypeScript client live here.
 * All reads are lazy (via getter functions) because API keys may be
 * injected via constructor config -- env is fallback only.
 */

// Helicone
export function getHeliconeApiKey(): string | undefined {
  return process.env["HELICONE_API_KEY"];
}
export function getHeliconeAnthropicBaseUrl(): string {
  return process.env["HELICONE_ANTHROPIC_BASE_URL"]?.trim() || "https://anthropic.helicone.ai/v1";
}
export function getHeliconeOpenaiBaseUrl(): string {
  return process.env["HELICONE_OPENAI_BASE_URL"]?.trim() || "https://oai.helicone.ai/v1";
}

// Provider API Keys
export function getOpenaiApiKey(): string | undefined {
  return process.env["OPENAI_API_KEY"];
}
export function getAnthropicApiKey(): string | undefined {
  return process.env["ANTHROPIC_API_KEY"];
}
export function getGeminiApiKey(): string | undefined {
  return process.env["GEMINI_API_KEY"];
}
export function getOpenrouterApiKey(): string | undefined {
  return process.env["OPENROUTER_API_KEY"];
}
export function getSnoLlmApiKey(): string | undefined {
  // LH: Current sno-gpu deployments still expose INTERNAL_SERVICE_SECRET
  // even when SNO_LLM_API_KEY is absent, so keep both env names valid.
  return process.env["SNO_LLM_API_KEY"] ?? process.env["INTERNAL_SERVICE_SECRET"];
}
export function getDeepinfraApiKey(): string | undefined {
  return process.env["DEEPINFRA_API_KEY"];
}
export function getNovitaApiKey(): string | undefined {
  return process.env["NOVITA_API_KEY"];
}
export function getTogetherApiKey(): string | undefined {
  return process.env["TOGETHER_API_KEY"];
}

// Environment
export function getNodeEnv(): string | undefined {
  return process.env["NODE_ENV"];
}

// GPU / Self-Hosted
export function getGpuBaseUrl(): string | undefined {
  return process.env["GPU_BASE_URL"];
}

// Client Config (env fallbacks for constructor defaults)
export function getCaptureTelemetryPayload(): string | undefined {
  return process.env["LLMIX_CAPTURE_TELEMETRY_PAYLOAD"];
}
export function getCallTimeoutMs(): string | undefined {
  return process.env["LLMIX_CALL_TIMEOUT_MS"];
}
