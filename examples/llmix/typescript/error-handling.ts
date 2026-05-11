/**
 * LLMix TypeScript error handling patterns.
 *
 * Demonstrates handling each failure mode with appropriate
 * recovery strategies and user-facing messages.
 *
 * Run with:
 *   OPENAI_API_KEY=sk-... bun run examples/llmix/typescript/error-handling.ts
 */

import {
  CallPipeline,
  KeyPool,
  openaiDispatch,
  ProviderError,
  BudgetExceededError,
  CircuitOpenError,
  TimeoutError,
  KeyPoolExhaustedError,
  type CallInput,
} from "@snoai/llmix";

async function safeCall(pipeline: CallPipeline, input: CallInput): Promise<string> {
  try {
    const response = await pipeline.call(input);
    return response.content;
  } catch (error: unknown) {
    if (error instanceof BudgetExceededError) {
      // Daily spend limit reached — notify billing, queue for later
      console.error(`[BUDGET] Daily limit reached: $${error.currentSpend}/$${error.limit}`);
      return "Service temporarily unavailable — usage limit reached.";
    }

    if (error instanceof CircuitOpenError) {
      // Provider is down — circuit breaker is protecting us
      console.error(`[CIRCUIT] ${error.provider} circuit open, cooldown: ${error.cooldownMs}ms`);
      return "The AI service is temporarily unavailable. Please retry shortly.";
    }

    if (error instanceof KeyPoolExhaustedError) {
      // All API keys are rate-limited or revoked
      console.error(`[KEYS] All keys exhausted for ${error.provider}`);
      return "Service capacity reached. Please try again in a few minutes.";
    }

    if (error instanceof TimeoutError) {
      // Request exceeded deadline
      console.error(`[TIMEOUT] Request timed out after ${error.deadlineMs}ms`);
      return "The request took too long. Please try a shorter prompt.";
    }

    if (error instanceof ProviderError) {
      // Catch-all for provider HTTP errors
      console.error(`[PROVIDER] ${error.provider} ${error.statusCode}: ${error.message}`);
      if (error.statusCode === 400) {
        return "Invalid request. Please check your input.";
      }
      return "An unexpected error occurred. Please retry.";
    }

    throw error; // Unknown error — let it bubble
  }
}

async function main(): Promise<void> {
  const pipeline = new CallPipeline({ dispatch: openaiDispatch() });
  pipeline.setKeyPool("openai", new KeyPool([process.env["OPENAI_API_KEY"]!]));

  const input: CallInput = {
    config: {
      provider: "openai",
      model: "gpt-4.1-mini",
      common: { temperature: 0.5, maxOutputTokens: 128 },
    },
    messages: [{ role: "user", content: "Hello!" }],
  };

  const result = await safeCall(pipeline, input);
  console.log(`Result: ${result}`);
}

main().catch(console.error);
