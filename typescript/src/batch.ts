/**
 * LLMix Batch Processing Module
 *
 * Batch ID encoding/decoding, durable metadata, and provider-specific
 * batch submit/status/results dispatch.
 */

import { createHash } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { resolveStateDir } from "./resilience";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BATCHES_SUBDIR = "batches";

export type BatchProvider = "openai" | "anthropic" | "gemini";

export type BatchState =
  | "pending"
  | "in_progress"
  | "completed"
  | "failed"
  | "expired";

// ---------------------------------------------------------------------------
// Batch ID Encoding / Decoding (Tasks 110, 111)
// ---------------------------------------------------------------------------

/**
 * Compute the key fingerprint: last 8 hex chars of SHA-256(apiKey).
 */
function keyFingerprint(apiKey: string): string {
  const hash = createHash("sha256").update(apiKey).digest("hex");
  return hash.slice(-8);
}

/**
 * Encode a batch ID from its components.
 *
 * Format: `{provider}:{keyFingerprint}:{nPrompts}:{rawBatchId}`
 */
export function encodeBatchId(
  provider: BatchProvider,
  apiKey: string,
  nPrompts: number,
  rawBatchId: string,
): string {
  const fp = keyFingerprint(apiKey);
  return `${provider}:${fp}:${nPrompts}:${rawBatchId}`;
}

/**
 * Decoded batch ID components.
 */
export interface DecodedBatchId {
  provider: BatchProvider;
  keyFingerprint: string;
  nPrompts: number;
  rawBatchId: string;
}

/**
 * Decode a batch ID string into its components.
 *
 * Splits on the first 3 colons only — rawBatchId may contain colons.
 */
export function decodeBatchId(batchId: string): DecodedBatchId {
  const firstColon = batchId.indexOf(":");
  if (firstColon === -1) throw new Error(`Invalid batch ID: ${batchId}`);

  const secondColon = batchId.indexOf(":", firstColon + 1);
  if (secondColon === -1) throw new Error(`Invalid batch ID: ${batchId}`);

  const thirdColon = batchId.indexOf(":", secondColon + 1);
  if (thirdColon === -1) throw new Error(`Invalid batch ID: ${batchId}`);

  const provider = batchId.slice(0, firstColon) as BatchProvider;
  const fp = batchId.slice(firstColon + 1, secondColon);
  const nPrompts = parseInt(batchId.slice(secondColon + 1, thirdColon), 10);
  const rawBatchId = batchId.slice(thirdColon + 1);

  if (!["openai", "anthropic", "gemini"].includes(provider)) {
    throw new Error(`Unknown batch provider: ${provider}`);
  }
  if (fp.length !== 8) {
    throw new Error(`Invalid key fingerprint length: ${fp}`);
  }
  if (isNaN(nPrompts) || nPrompts < 1) {
    throw new Error(`Invalid nPrompts: ${batchId.slice(secondColon + 1, thirdColon)}`);
  }
  if (!rawBatchId) {
    throw new Error(`Empty rawBatchId in: ${batchId}`);
  }

  return { provider, keyFingerprint: fp, nPrompts, rawBatchId };
}

// ---------------------------------------------------------------------------
// Schemas (Task 113)
// ---------------------------------------------------------------------------

export interface BatchStatus {
  batchId: string;
  state: BatchState;
  totalRequests: number;
  completedRequests: number;
  failedRequests: number;
}

export interface BatchResult {
  index: number;
  success: boolean;
  response?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Durable Metadata (Task 112)
// ---------------------------------------------------------------------------

export interface BatchMetadata {
  keyFingerprint: string;
  provider: BatchProvider;
  nPrompts: number;
  submittedAt: string;
}

function batchesDir(stateDir?: string): string {
  const base = stateDir ?? resolveStateDir();
  return join(base, BATCHES_SUBDIR);
}

function metadataFilename(batchId: string): string {
  return `${Buffer.from(batchId, "utf8").toString("base64url")}.json`;
}

function metadataPath(batchId: string, stateDir?: string): string {
  return join(batchesDir(stateDir), metadataFilename(batchId));
}

export async function writeMetadata(
  batchId: string,
  apiKey: string,
  provider: BatchProvider,
  nPrompts: number,
  stateDir?: string,
): Promise<void> {
  const dir = batchesDir(stateDir);
  await mkdir(dir, { recursive: true });

  const metadata: BatchMetadata = {
    keyFingerprint: keyFingerprint(apiKey),
    provider,
    nPrompts,
    submittedAt: new Date().toISOString(),
  };

  // Atomic write: write to temp file then rename to avoid corruption on crash
  const finalPath = metadataPath(batchId, stateDir);
  const tmpPath = finalPath + ".tmp";
  await writeFile(tmpPath, JSON.stringify(metadata), "utf-8");
  await rename(tmpPath, finalPath);
}

export async function readMetadata(
  batchId: string,
  stateDir?: string,
): Promise<BatchMetadata> {
  const raw = await readFile(metadataPath(batchId, stateDir), "utf-8");
  return JSON.parse(raw) as BatchMetadata;
}

export async function deleteMetadata(
  batchId: string,
  stateDir?: string,
): Promise<void> {
  await rm(metadataPath(batchId, stateDir), { force: true });
}

// ---------------------------------------------------------------------------
// Provider-specific batch operations (Tasks 107, 108, 109)
// ---------------------------------------------------------------------------

// --- OpenAI (Task 107) ---

interface OpenAIBatchRequest {
  custom_id: string;
  method: string;
  url: string;
  body: Record<string, unknown>;
}

async function openaiSubmit(
  apiKey: string,
  model: string,
  prompts: string[],
  systemPrompt?: string,
  params?: Record<string, unknown>,
): Promise<string> {
  const lines: string[] = [];
  for (let i = 0; i < prompts.length; i++) {
    const messages: Array<{ role: string; content: string }> = [];
    if (systemPrompt) messages.push({ role: "system", content: systemPrompt });
    messages.push({ role: "user", content: prompts[i] as string });
    const body: Record<string, unknown> = { model, messages, ...params };
    const req: OpenAIBatchRequest = {
      custom_id: `req-${i}`,
      method: "POST",
      url: "/v1/chat/completions",
      body,
    };
    lines.push(JSON.stringify(req));
  }

  const jsonlContent = lines.join("\n") + "\n";
  const blob = new Blob([jsonlContent], { type: "application/jsonl" });
  const formData = new FormData();
  formData.append("purpose", "batch");
  formData.append("file", blob, "batch.jsonl");

  const uploadRes = await fetch("https://api.openai.com/v1/files", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}` },
    body: formData,
    signal: AbortSignal.timeout(60_000),
  });
  if (!uploadRes.ok) {
    throw new Error(`OpenAI file upload failed: ${uploadRes.status} ${await uploadRes.text()}`);
  }
  const fileObj = (await uploadRes.json()) as { id: string };

  const batchRes = await fetch("https://api.openai.com/v1/batches", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      input_file_id: fileObj.id,
      endpoint: "/v1/chat/completions",
      completion_window: "24h",
    }),
    signal: AbortSignal.timeout(60_000),
  });
  if (!batchRes.ok) {
    throw new Error(`OpenAI batch create failed: ${batchRes.status} ${await batchRes.text()}`);
  }
  const batch = (await batchRes.json()) as { id: string };
  return batch.id;
}

async function openaiStatus(apiKey: string, rawBatchId: string): Promise<BatchStatus> {
  const res = await fetch(`https://api.openai.com/v1/batches/${rawBatchId}`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    throw new Error(`OpenAI batch status failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as {
    id: string;
    status: string;
    request_counts?: { total: number; completed: number; failed: number };
  };

  const stateMap: Record<string, BatchState> = {
    validating: "pending",
    in_progress: "in_progress",
    finalizing: "in_progress",
    completed: "completed",
    failed: "failed",
    expired: "expired",
    cancelled: "failed",
    cancelling: "in_progress",
  };

  return {
    batchId: batch.id,
    state: stateMap[batch.status] ?? "pending",
    totalRequests: batch.request_counts?.total ?? 0,
    completedRequests: batch.request_counts?.completed ?? 0,
    failedRequests: batch.request_counts?.failed ?? 0,
  };
}

async function openaiResults(
  apiKey: string,
  rawBatchId: string,
  nPrompts: number,
): Promise<BatchResult[]> {
  const res = await fetch(`https://api.openai.com/v1/batches/${rawBatchId}`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    throw new Error(`OpenAI batch retrieve failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as {
    status: string;
    output_file_id?: string;
  };

  if (batch.status !== "completed") {
    if (["failed", "expired", "cancelled"].includes(batch.status)) {
      throw new Error(`OpenAI batch ${rawBatchId} ${batch.status}`);
    }
    return [];
  }

  if (!batch.output_file_id) {
    throw new Error(`OpenAI batch ${rawBatchId} completed but no output_file_id`);
  }

  const contentRes = await fetch(
    `https://api.openai.com/v1/files/${batch.output_file_id}/content`,
    { headers: { Authorization: `Bearer ${apiKey}` }, signal: AbortSignal.timeout(60_000) },
  );
  if (!contentRes.ok) {
    throw new Error(`OpenAI file download failed: ${contentRes.status}`);
  }
  const text = await contentRes.text();

  const resultMap = new Map<number, BatchResult>();
  for (const line of text.trim().split("\n")) {
    if (!line) continue;
    const entry = JSON.parse(line) as {
      custom_id: string;
      response?: { status_code: number; body: { choices: Array<{ message: { content: string } }> } };
      error?: { message: string };
    };
    const idx = parseInt(entry.custom_id.replace("req-", ""), 10);
    if (entry.error) {
      resultMap.set(idx, { index: idx, success: false, error: entry.error.message });
    } else if (entry.response?.status_code !== 200) {
      resultMap.set(idx, {
        index: idx,
        success: false,
        error: `OpenAI batch entry returned status ${entry.response?.status_code ?? "unknown"}`,
      });
    } else {
      const content = entry.response.body.choices
        .map((c) => c.message.content || "")
        .join("");
      resultMap.set(idx, { index: idx, success: true, response: content });
    }
  }

  const results: BatchResult[] = [];
  for (let i = 0; i < nPrompts; i++) {
    results.push(resultMap.get(i) ?? { index: i, success: false, error: "Missing result" });
  }
  return results;
}

// --- Anthropic (Task 108) ---

async function anthropicSubmit(
  apiKey: string,
  model: string,
  prompts: string[],
  systemPrompt?: string,
  params?: Record<string, unknown>,
): Promise<string> {
  const maxTokens = (params?.["max_tokens"] as number | undefined) ?? 4096;
  const requests = prompts.map((prompt, i) => {
    const reqParams: Record<string, unknown> = {
      model,
      max_tokens: maxTokens,
      messages: [{ role: "user", content: prompt }],
    };
    if (systemPrompt) reqParams["system"] = systemPrompt;
    if (params) {
      const { max_tokens: _mt, ...rest } = params;
      Object.assign(reqParams, rest);
    }
    return { custom_id: `req-${i}`, params: reqParams };
  });

  const res = await fetch("https://api.anthropic.com/v1/messages/batches", {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "Content-Type": "application/json",
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({ requests }),
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    throw new Error(`Anthropic batch create failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as { id: string };
  return batch.id;
}

async function anthropicStatus(apiKey: string, rawBatchId: string): Promise<BatchStatus> {
  const res = await fetch(`https://api.anthropic.com/v1/messages/batches/${rawBatchId}`, {
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    throw new Error(`Anthropic batch status failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as {
    id: string;
    processing_status: string;
    request_counts?: {
      processing: number;
      succeeded: number;
      errored: number;
      canceled: number;
      expired: number;
    };
  };

  const counts = batch.request_counts;

  // Anthropic batches don't have a "pending" state — they transition directly
  // to "in_progress" on creation, so all non-"ended" states map to "in_progress".
  let state: BatchState;
  if (batch.processing_status === "ended") {
    // Distinguish all-failed from completed
    if (counts && counts.succeeded === 0 && counts.errored > 0) {
      state = "failed";
    } else {
      state = "completed";
    }
  } else if (batch.processing_status === "canceling") {
    state = "in_progress";
  } else {
    state = "in_progress";
  }

  const total = counts
    ? counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
    : 0;

  return {
    batchId: rawBatchId,
    state,
    totalRequests: total,
    completedRequests: counts?.succeeded ?? 0,
    failedRequests: (counts?.errored ?? 0) + (counts?.canceled ?? 0) + (counts?.expired ?? 0),
  };
}

async function anthropicResults(
  apiKey: string,
  rawBatchId: string,
  nPrompts: number,
): Promise<BatchResult[]> {
  const res = await fetch(`https://api.anthropic.com/v1/messages/batches/${rawBatchId}/results`, {
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    throw new Error(`Anthropic batch results failed: ${res.status} ${await res.text()}`);
  }
  const text = await res.text();

  const resultMap = new Map<number, BatchResult>();
  for (const line of text.trim().split("\n")) {
    if (!line) continue;
    const entry = JSON.parse(line) as {
      custom_id: string;
      result: {
        type: string;
        message?: { content: Array<{ type: string; text?: string }> };
        error?: { message: string };
      };
    };
    const idx = parseInt(entry.custom_id.replace("req-", ""), 10);
    if (entry.result.type === "succeeded" && entry.result.message) {
      const content = entry.result.message.content
        .filter((b) => b.type === "text" && b.text)
        .map((b) => b.text!)
        .join("");
      resultMap.set(idx, { index: idx, success: true, response: content });
    } else {
      resultMap.set(idx, {
        index: idx,
        success: false,
        error: entry.result.error?.message ?? "Unknown error",
      });
    }
  }

  const results: BatchResult[] = [];
  for (let i = 0; i < nPrompts; i++) {
    results.push(resultMap.get(i) ?? { index: i, success: false, error: "Missing result" });
  }
  return results;
}

// --- Gemini (Task 109) ---

async function geminiSubmit(
  apiKey: string,
  model: string,
  prompts: string[],
  systemPrompt?: string,
  params?: Record<string, unknown>,
): Promise<string> {
  const requests = prompts.map((prompt, i) => ({
    request: {
      model: `models/${model}`,
      contents: [{ parts: [{ text: prompt }] }],
      ...(systemPrompt
        ? { system_instruction: { parts: [{ text: systemPrompt }] } }
        : {}),
      generationConfig: params ?? {},
    },
    metadata: { id: `req-${i}` },
  }));

  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${model}:batchGenerateContent?key=${apiKey}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requests }),
      signal: AbortSignal.timeout(60_000),
    },
  );
  if (!res.ok) {
    throw new Error(`Gemini batch create failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as { name: string };
  return batch.name;
}

async function geminiStatus(apiKey: string, rawBatchId: string): Promise<BatchStatus> {
  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/${rawBatchId}?key=${apiKey}`,
    { signal: AbortSignal.timeout(60_000) },
  );
  if (!res.ok) {
    throw new Error(`Gemini batch status failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as {
    name: string;
    state: string;
    completionStats?: { successfulCount?: number; failedCount?: number; incompleteCount?: number };
  };

  const stateMap: Record<string, BatchState> = {
    JOB_STATE_SUCCEEDED: "completed",
    JOB_STATE_FAILED: "failed",
    JOB_STATE_CANCELLED: "failed",
    JOB_STATE_EXPIRED: "expired",
    JOB_STATE_PENDING: "pending",
    JOB_STATE_RUNNING: "in_progress",
  };

  return {
    batchId: rawBatchId,
    state: stateMap[batch.state] ?? "in_progress",
    totalRequests:
      (batch.completionStats?.successfulCount ?? 0) +
      (batch.completionStats?.failedCount ?? 0) +
      (batch.completionStats?.incompleteCount ?? 0),
    completedRequests: batch.completionStats?.successfulCount ?? 0,
    failedRequests: batch.completionStats?.failedCount ?? 0,
  };
}

async function geminiResults(
  apiKey: string,
  rawBatchId: string,
  nPrompts: number,
): Promise<BatchResult[]> {
  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/${rawBatchId}?key=${apiKey}`,
    { signal: AbortSignal.timeout(60_000) },
  );
  if (!res.ok) {
    throw new Error(`Gemini batch results failed: ${res.status} ${await res.text()}`);
  }
  const batch = (await res.json()) as {
    state: string;
    responses?: Array<{
      metadata?: { id?: string };
      response?: { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }> };
      error?: { message: string };
    }>;
  };

  if (!["JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"].includes(batch.state)) {
    if (["JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"].includes(batch.state)) {
      throw new Error(`Gemini batch ${rawBatchId} ${batch.state}`);
    }
    return [];
  }

  const resultMap = new Map<number, BatchResult>();
  if (batch.responses) {
    for (const resp of batch.responses) {
      const idStr = resp.metadata?.id ?? "";
      const idx = parseInt(idStr.replace("req-", ""), 10);
      if (isNaN(idx)) continue;

      if (resp.error) {
        resultMap.set(idx, { index: idx, success: false, error: resp.error.message });
      } else {
        const text = resp.response?.candidates?.[0]?.content?.parts?.[0]?.text ?? "";
        resultMap.set(idx, { index: idx, success: true, response: text });
      }
    }
  }

  const results: BatchResult[] = [];
  for (let i = 0; i < nPrompts; i++) {
    results.push(resultMap.get(i) ?? { index: i, success: false, error: "Missing result" });
  }
  return results;
}

// ---------------------------------------------------------------------------
// BatchProcessor (Tasks 105/106)
// ---------------------------------------------------------------------------

export interface BatchSubmitOptions {
  provider: BatchProvider;
  apiKey: string;
  model: string;
  prompts: string[];
  systemPrompt?: string;
  params?: Record<string, unknown>;
  stateDir?: string;
}

export interface BatchReadOptions {
  stateDir?: string;
}

export class BatchProcessor {
  private readonly stateDir?: string;

  constructor(options?: { stateDir?: string }) {
    if (options?.stateDir !== undefined) {
      this.stateDir = options.stateDir;
    }
  }

  /**
   * Submit a batch of prompts. Returns an encoded batch ID.
   */
  async submit(options: BatchSubmitOptions): Promise<string> {
    const { provider, apiKey, model, prompts, systemPrompt, params } = options;
    if (prompts.length === 0) throw new Error("prompts must not be empty");
    const effectiveStateDir = options.stateDir ?? this.stateDir;

    let rawBatchId: string;
    switch (provider) {
      case "openai":
        rawBatchId = await openaiSubmit(apiKey, model, prompts, systemPrompt, params);
        break;
      case "anthropic":
        rawBatchId = await anthropicSubmit(apiKey, model, prompts, systemPrompt, params);
        break;
      case "gemini":
        rawBatchId = await geminiSubmit(apiKey, model, prompts, systemPrompt, params);
        break;
      default:
        throw new Error(`Unsupported batch provider: ${provider as string}`);
    }

    const batchId = encodeBatchId(provider, apiKey, prompts.length, rawBatchId);
    await writeMetadata(batchId, apiKey, provider, prompts.length, effectiveStateDir);
    return batchId;
  }

  /**
   * Check the status of a batch job.
   *
   * @param batchId - Encoded batch ID
   * @param apiKey - API key for the provider (must match the key used at submit time)
   */
  async status(
    batchId: string,
    apiKey: string,
    options?: BatchReadOptions,
  ): Promise<BatchStatus> {
    const decoded = decodeBatchId(batchId);
    const effectiveStateDir = options?.stateDir ?? this.stateDir;
    const metadata = await readMetadata(batchId, effectiveStateDir);

    // Verify the provided key matches the fingerprint stored at submit time
    const fp = keyFingerprint(apiKey);
    if (fp !== metadata.keyFingerprint) {
      throw new Error(
        `API key fingerprint mismatch: provided key does not match the key used at submit time`,
      );
    }

    switch (decoded.provider) {
      case "openai":
        return openaiStatus(apiKey, decoded.rawBatchId);
      case "anthropic":
        return anthropicStatus(apiKey, decoded.rawBatchId);
      case "gemini":
        return geminiStatus(apiKey, decoded.rawBatchId);
      default:
        throw new Error(`Unsupported batch provider: ${decoded.provider as string}`);
    }
  }

  /**
   * Retrieve batch results. Deletes metadata after successful retrieval.
   *
   * @param batchId - Encoded batch ID
   * @param apiKey - API key for the provider (must match the key used at submit time)
   */
  async results(
    batchId: string,
    apiKey: string,
    options?: BatchReadOptions,
  ): Promise<BatchResult[]> {
    const decoded = decodeBatchId(batchId);
    const effectiveStateDir = options?.stateDir ?? this.stateDir;
    const metadata = await readMetadata(batchId, effectiveStateDir);

    // Verify the provided key matches the fingerprint stored at submit time
    const fp = keyFingerprint(apiKey);
    if (fp !== metadata.keyFingerprint) {
      throw new Error(
        `API key fingerprint mismatch: provided key does not match the key used at submit time`,
      );
    }

    let results: BatchResult[];
    switch (decoded.provider) {
      case "openai":
        results = await openaiResults(apiKey, decoded.rawBatchId, decoded.nPrompts);
        break;
      case "anthropic":
        results = await anthropicResults(apiKey, decoded.rawBatchId, decoded.nPrompts);
        break;
      case "gemini":
        results = await geminiResults(apiKey, decoded.rawBatchId, decoded.nPrompts);
        break;
      default:
        throw new Error(`Unsupported batch provider: ${decoded.provider as string}`);
    }

    if (results.length > 0) {
      await deleteMetadata(batchId, effectiveStateDir);
    }
    return results;
  }
}

// ---------------------------------------------------------------------------
// E2E Test Stub (Task 116 — out of scope)
// ---------------------------------------------------------------------------

// TODO: E2E tests with real API keys (Task 116)
// - OpenAI batch submit/status/results roundtrip
// - Anthropic batch submit/status/results roundtrip
// - Gemini batch submit/status/results roundtrip
