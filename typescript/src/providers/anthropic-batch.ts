/**
 * Anthropic Batch Adapter
 *
 * Uses the raw `anthropic` npm package (not AI SDK) for batch operations.
 * The Anthropic Message Batches API allows submitting up to 100,000 requests
 * at 50% cost with a 24-hour turnaround.
 *
 * @see https://docs.anthropic.com/en/docs/build-with-claude/batch-processing
 */

import Anthropic from "@anthropic-ai/sdk";

/** A single request in an Anthropic batch */
export interface AnthropicBatchRequest {
  /** Unique identifier for this request within the batch */
  customId: string;

  /** Anthropic model ID */
  model: string;

  /** Max tokens for the response */
  maxTokens: number;

  /** Messages array (Anthropic format) */
  messages: Array<{
    role: "user" | "assistant";
    content: string;
  }>;

  /** Optional system prompt */
  system?: string;

  /** Optional temperature */
  temperature?: number;
}

/** Batch status from Anthropic API */
export interface AnthropicBatchStatus {
  /** Batch ID */
  id: string;

  /** Processing status */
  processingStatus: "in_progress" | "ended" | "canceling" | "canceled";

  /** Request counts */
  requestCounts: {
    processing: number;
    succeeded: number;
    errored: number;
    canceled: number;
    expired: number;
  };

  /** Creation timestamp */
  createdAt: string;

  /** Completion timestamp (null if not complete) */
  endedAt: string | null;

  /** Expiration timestamp */
  expiresAt: string;
}

/** A single result from a completed batch */
export interface AnthropicBatchResult {
  /** The custom_id from the request */
  customId: string;

  /** Result type: succeeded, errored, canceled, or expired */
  resultType: "succeeded" | "errored" | "canceled" | "expired";

  /** Response content (only if succeeded) */
  content?: string;

  /** Error message (only if errored) */
  error?: string;

  /** Usage (only if succeeded) */
  usage?: {
    inputTokens: number;
    outputTokens: number;
  };

  /** Model used */
  model?: string;
}

/**
 * Submit a batch of requests to Anthropic's Message Batches API.
 *
 * @param apiKey - Anthropic API key
 * @param requests - Array of batch requests
 * @returns Batch ID and initial status
 */
export async function submitBatch(
  apiKey: string,
  requests: AnthropicBatchRequest[]
): Promise<AnthropicBatchStatus> {
  const client = new Anthropic({ apiKey });

  const batchRequests = requests.map((req) => ({
    custom_id: req.customId,
    params: {
      model: req.model,
      max_tokens: req.maxTokens,
      messages: req.messages,
      ...(req.system !== undefined && { system: req.system }),
      ...(req.temperature !== undefined && { temperature: req.temperature }),
    },
  }));

  const batch = await client.messages.batches.create({
    requests: batchRequests,
  });

  return mapBatchStatus(batch);
}

/**
 * Get the status of a batch.
 *
 * @param apiKey - Anthropic API key
 * @param batchId - Batch ID to check
 * @returns Current batch status
 */
export async function getBatchStatus(
  apiKey: string,
  batchId: string
): Promise<AnthropicBatchStatus> {
  const client = new Anthropic({ apiKey });
  const batch = await client.messages.batches.retrieve(batchId);
  return mapBatchStatus(batch);
}

/**
 * Get results from a completed batch.
 *
 * @param apiKey - Anthropic API key
 * @param batchId - Batch ID to retrieve results for
 * @returns Array of batch results
 */
export async function getBatchResults(
  apiKey: string,
  batchId: string
): Promise<AnthropicBatchResult[]> {
  const client = new Anthropic({ apiKey });
  const results: AnthropicBatchResult[] = [];

  const resultStream = await client.messages.batches.results(batchId);
  for await (const entry of resultStream) {
    const result: AnthropicBatchResult = {
      customId: entry.custom_id,
      resultType: entry.result.type,
    };

    if (entry.result.type === "succeeded") {
      const message = entry.result.message;
      // Extract text content
      const textParts: string[] = [];
      for (const block of message.content) {
        if (block.type === "text") {
          textParts.push(block.text);
        }
      }
      result.content = textParts.join("");
      result.model = message.model;
      result.usage = {
        inputTokens: message.usage.input_tokens,
        outputTokens: message.usage.output_tokens,
      };
    } else if (entry.result.type === "errored") {
      const errorResult = entry.result.error;
      result.error = (errorResult as { message?: string }).message ?? "Unknown error";
    }

    results.push(result);
  }

  return results;
}

// Internal helper to map Anthropic SDK batch object to our interface
function mapBatchStatus(batch: Anthropic.Messages.Batches.MessageBatch): AnthropicBatchStatus {
  return {
    id: batch.id,
    processingStatus: batch.processing_status,
    requestCounts: {
      processing: batch.request_counts.processing,
      succeeded: batch.request_counts.succeeded,
      errored: batch.request_counts.errored,
      canceled: batch.request_counts.canceled,
      expired: batch.request_counts.expired,
    },
    createdAt: batch.created_at,
    endedAt: batch.ended_at,
    expiresAt: batch.expires_at,
  };
}
