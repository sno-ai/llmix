/**
 * Tests for the LLMix batch processing module (TypeScript).
 *
 * Covers batch ID encode/decode roundtrip, colon-safe decode, and
 * durable metadata file create/read/cleanup.
 * Uses shared fixtures from fixtures/llmix/batch-id-roundtrip.json.
 */

import { readFileSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  encodeBatchId,
  decodeBatchId,
  writeMetadata,
  readMetadata,
  deleteMetadata,
  BatchProcessor,
  type BatchProvider,
} from "../src/batch.js";

const fixtureDir = resolve(import.meta.dirname, "..", "..", "..", "..", "fixtures", "llmix");
const fixturesPath = resolve(fixtureDir, "batch-id-roundtrip.json");

interface RoundtripScenario {
  provider: BatchProvider;
  apiKey: string;
  nPrompts: number;
  rawBatchId: string;
  note: string;
}

interface InvalidScenario {
  batchId: string;
  reason: string;
}

interface MetadataScenario {
  batchId: string;
  apiKey: string;
  provider: BatchProvider;
  nPrompts: number;
  note: string;
}

interface Fixtures {
  roundtrips: RoundtripScenario[];
  invalidBatchIds: InvalidScenario[];
  metadataScenarios: MetadataScenario[];
}

const fixtures: Fixtures = JSON.parse(readFileSync(fixturesPath, "utf-8"));

let passed = 0;
let failed = 0;

function assert(condition: boolean, msg: string): void {
  if (condition) {
    passed++;
    console.log(`[PASS] ${msg}`);
  } else {
    failed++;
    console.log(`[FAIL] ${msg}`);
  }
}

function assertThrows(fn: () => void, msg: string): void {
  try {
    fn();
    failed++;
    console.log(`[FAIL] ${msg} — expected error but none thrown`);
  } catch {
    passed++;
    console.log(`[PASS] ${msg}`);
  }
}

function keyFingerprint(apiKey: string): string {
  return createHash("sha256").update(apiKey).digest("hex").slice(-8);
}

function metadataFilename(batchId: string): string {
  return `${Buffer.from(batchId, "utf8").toString("base64url")}.json`;
}

// ---------------------------------------------------------------------------
// Batch ID encode/decode roundtrip (Tasks 110, 111, 115)
// ---------------------------------------------------------------------------

console.log("\n=== Batch ID encode/decode roundtrip ===");

for (const scenario of fixtures.roundtrips) {
  const batchId = encodeBatchId(
    scenario.provider,
    scenario.apiKey,
    scenario.nPrompts,
    scenario.rawBatchId,
  );

  // Verify format: provider:fingerprint:nPrompts:rawBatchId
  const expectedFp = keyFingerprint(scenario.apiKey);
  const expectedId = `${scenario.provider}:${expectedFp}:${scenario.nPrompts}:${scenario.rawBatchId}`;
  assert(batchId === expectedId, `encode format: ${scenario.note}`);

  // Decode and verify all fields
  const decoded = decodeBatchId(batchId);
  assert(decoded.provider === scenario.provider, `roundtrip provider: ${scenario.note}`);
  assert(decoded.keyFingerprint === expectedFp, `roundtrip fingerprint: ${scenario.note}`);
  assert(decoded.nPrompts === scenario.nPrompts, `roundtrip nPrompts: ${scenario.note}`);
  assert(decoded.rawBatchId === scenario.rawBatchId, `roundtrip rawBatchId: ${scenario.note}`);
}

// ---------------------------------------------------------------------------
// Colon-safe decode
// ---------------------------------------------------------------------------

console.log("\n=== Colon-safe decode ===");

// The fixture with colons in rawBatchId
const colonScenario = fixtures.roundtrips.find((s) =>
  s.rawBatchId.includes(":"),
);
if (colonScenario) {
  const batchId = encodeBatchId(
    colonScenario.provider,
    colonScenario.apiKey,
    colonScenario.nPrompts,
    colonScenario.rawBatchId,
  );
  const decoded = decodeBatchId(batchId);
  assert(
    decoded.rawBatchId === colonScenario.rawBatchId,
    `colon-safe: rawBatchId preserved with colons ("${colonScenario.rawBatchId}")`,
  );
  // Count colons in rawBatchId to confirm they're preserved
  const colonCount = (decoded.rawBatchId.match(/:/g) || []).length;
  assert(
    colonCount === (colonScenario.rawBatchId.match(/:/g) || []).length,
    `colon-safe: colon count matches (${colonCount})`,
  );
}

// ---------------------------------------------------------------------------
// Invalid batch IDs
// ---------------------------------------------------------------------------

console.log("\n=== Invalid batch IDs ===");

for (const scenario of fixtures.invalidBatchIds) {
  assertThrows(
    () => decodeBatchId(scenario.batchId),
    `rejects invalid: ${scenario.reason}`,
  );
}

// ---------------------------------------------------------------------------
// Durable metadata (Task 112, 115)
// ---------------------------------------------------------------------------

console.log("\n=== Durable metadata ===");

const testDir = join(tmpdir(), `llmix-batch-test-${Date.now()}`);
mkdirSync(testDir, { recursive: true });

async function runMetadataTests(): Promise<void> {
  for (const scenario of fixtures.metadataScenarios) {
    // Write
    await writeMetadata(
      scenario.batchId,
      scenario.apiKey,
      scenario.provider,
      scenario.nPrompts,
      testDir,
    );

    // Check file exists
    const expectedPath = join(testDir, "batches", metadataFilename(scenario.batchId));
    assert(existsSync(expectedPath), `metadata file created: ${scenario.note}`);

    // Read
    const metadata = await readMetadata(scenario.batchId, testDir);
    assert(metadata.keyFingerprint === keyFingerprint(scenario.apiKey), `metadata keyFingerprint preserved: ${scenario.note}`);
    assert(metadata.provider === scenario.provider, `metadata provider preserved: ${scenario.note}`);
    assert(metadata.nPrompts === scenario.nPrompts, `metadata nPrompts preserved: ${scenario.note}`);
    assert(typeof metadata.submittedAt === "string", `metadata submittedAt is string: ${scenario.note}`);

    // Delete
    await deleteMetadata(scenario.batchId, testDir);
    assert(!existsSync(expectedPath), `metadata file deleted: ${scenario.note}`);

    // Delete again (idempotent)
    await deleteMetadata(scenario.batchId, testDir);
    assert(true, `metadata double-delete is safe: ${scenario.note}`);
  }

  const geminiBatchId = encodeBatchId("gemini", "gemini-key", 1, "operations/abc123");
  await writeMetadata(geminiBatchId, "gemini-key", "gemini", 1, testDir);
  const geminiPath = join(testDir, "batches", metadataFilename(geminiBatchId));
  assert(existsSync(geminiPath), "metadata filename escapes Gemini path separators");
}

await runMetadataTests();

// ---------------------------------------------------------------------------
// BatchProcessor metadata lifecycle
// ---------------------------------------------------------------------------

console.log("\n=== BatchProcessor metadata lifecycle ===");

async function runBatchProcessorMetadataLifecycleTest(): Promise<void> {
  const stateDir = join(tmpdir(), `llmix-batch-processor-${Date.now()}`);
  mkdirSync(stateDir, { recursive: true });

  const processor = new BatchProcessor();
  const apiKey = "sk-test-roundtrip";
  const batchId = "batch-123";
  const metadataBatchId = `openai:${keyFingerprint(apiKey)}:1:${batchId}`;
  const metadataPath = join(stateDir, "batches", metadataFilename(metadataBatchId));
  const originalFetch = globalThis.fetch;

  let callIndex = 0;
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
    callIndex++;

    switch (callIndex) {
      case 1:
        assert(url === "https://api.openai.com/v1/files", "submit uploads batch file");
        return new Response(JSON.stringify({ id: "file-123" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      case 2:
        assert(url === "https://api.openai.com/v1/batches", "submit creates batch");
        return new Response(JSON.stringify({ id: batchId }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      case 3:
        assert(url === `https://api.openai.com/v1/batches/${batchId}`, "status reads provider batch state");
        return new Response(JSON.stringify({
          id: batchId,
          status: "in_progress",
          request_counts: { total: 1, completed: 0, failed: 0 },
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      case 4:
        assert(url === `https://api.openai.com/v1/batches/${batchId}`, "pending results poll reuses provider batch state");
        return new Response(JSON.stringify({ status: "in_progress" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      case 5:
        assert(url === `https://api.openai.com/v1/batches/${batchId}`, "terminal results poll fetches completed batch");
        return new Response(JSON.stringify({ status: "completed", output_file_id: "file-out-123" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      case 6:
        assert(url === "https://api.openai.com/v1/files/file-out-123/content", "terminal results download output file");
        return new Response(
          `${JSON.stringify({
            custom_id: "req-0",
            response: {
              status_code: 200,
              body: { choices: [{ message: { content: "done" } }] },
            },
          })}\n`,
          {
            status: 200,
            headers: { "Content-Type": "application/jsonl" },
          },
        );
      default:
        throw new Error(`Unexpected fetch call #${callIndex}: ${url}`);
    }
  }) as typeof fetch;

  try {
    const submittedBatchId = await processor.submit({
      provider: "openai",
      apiKey,
      model: "gpt-4o-mini",
      prompts: ["hello"],
      stateDir,
    });
    assert(submittedBatchId === metadataBatchId, "submit encodes batch ID with metadata fingerprint");
    assert(existsSync(metadataPath), "submit writes metadata to per-call stateDir");

    const status = await processor.status(submittedBatchId, apiKey, { stateDir });
    assert(status.state === "in_progress", "status reads metadata from per-call stateDir");

    const pendingResults = await processor.results(submittedBatchId, apiKey, { stateDir });
    assert(pendingResults.length === 0, "pending results returns empty list");
    assert(existsSync(metadataPath), "pending results preserves metadata for later polls");

    const completedResults = await processor.results(submittedBatchId, apiKey, { stateDir });
    assert(completedResults.length === 1, "terminal results returns provider output");
    assert(completedResults[0]?.response === "done", "terminal results returns decoded content");
    assert(!existsSync(metadataPath), "terminal results deletes metadata after successful retrieval");
  } finally {
    globalThis.fetch = originalFetch;
    rmSync(stateDir, { recursive: true, force: true });
  }
}

await runBatchProcessorMetadataLifecycleTest();

// Cleanup
rmSync(testDir, { recursive: true, force: true });

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log(`\n=== Summary: ${passed} passed, ${failed} failed ===`);
if (failed > 0) {
  process.exit(1);
}
