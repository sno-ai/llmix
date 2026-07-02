/**
 * HTTP/2 Transport Configuration
 *
 * Provider transport registry and configuration for HTTP/2 vs HTTP/1.1.
 *
 * Investigation results (Tasks 42-44):
 *
 * OpenAI (Task 42-43):
 *   AI SDK doesn't directly expose an HTTP/2 transport toggle.
 *   To use HTTP/2 with OpenAI in Node.js, options are:
 *   1. Use the `openai` SDK directly with a custom `fetch` that uses undici HTTP/2
 *   2. Override the `fetch` option on the AI SDK provider
 *   3. Use Node.js built-in `http2` module via a custom fetch wrapper
 *   For now this module declares the *intent* (http2: true for OpenAI) and
 *   provides a stub for the actual transport creation. The Python side gets
 *   real HTTP/2 via httpx; TypeScript will follow once AI SDK exposes transport
 *   hooks or we adopt undici directly.
 *
 * Gemini (Task 44):
 *   @ai-sdk/google doesn't expose transport override. The underlying google-genai
 *   SDK in TypeScript uses standard fetch (HTTP/1.1 in Node.js). HTTP/1.1 is the
 *   accepted tradeoff for TypeScript Gemini. Python Gemini gets HTTP/2 for free
 *   via httpx[http2].
 */

// ---------------------------------------------------------------------------
// Provider transport registry
// ---------------------------------------------------------------------------

export interface ProviderTransportConfig {
  /** Provider identifier */
  readonly name: string;
  /** Whether this provider should use HTTP/2 when available */
  readonly http2: boolean;
}

/**
 * Transport configuration for each known provider.
 *
 * - OpenAI: HTTP/2 desired (multiplexing benefits for streaming)
 * - Gemini: HTTP/1.1 in TS (SDK doesn't expose transport override)
 * - Proxy providers (OpenRouter, Helicone): HTTP/1.1 (proxy compatibility)
 */
export const PROVIDER_TRANSPORT: Readonly<Record<string, ProviderTransportConfig>> = {
  openai: { name: "openai", http2: true },
  anthropic: { name: "anthropic", http2: false },
  gemini: { name: "gemini", http2: false }, // TS limitation -- see Task 44 comment above
  deepseek: { name: "deepseek", http2: false },
  openrouter: { name: "openrouter", http2: false },
  helicone: { name: "helicone", http2: false },
} as const;

/**
 * Look up transport config for a provider.
 * Returns http2: false for unknown providers.
 */
export function getProviderTransport(provider: string): ProviderTransportConfig {
  return PROVIDER_TRANSPORT[provider] ?? { name: provider, http2: false };
}

// ---------------------------------------------------------------------------
// OpenAI transport stub (Task 42-43)
// ---------------------------------------------------------------------------

/**
 * Stub for creating an HTTP/2-capable transport for OpenAI.
 *
 * Current status: NOT YET IMPLEMENTED.
 *
 * Approach when implemented:
 * ```ts
 * import { Agent } from "undici";
 * const agent = new Agent({ allowH2: true });
 * // Pass as fetch override to openai SDK or AI SDK provider
 * ```
 *
 * For now, returns undefined (callers should fall back to default fetch).
 * The Python side uses httpx.AsyncClient(http2=True) which works today.
 */
export function createOpenAITransport(): undefined {
  // TODO: Implement once AI SDK exposes an HTTP/2 transport hook or we adopt undici.
  // See investigation notes in module docstring.
  return undefined;
}
