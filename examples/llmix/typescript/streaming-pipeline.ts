/**
 * LLMix TypeScript streaming pipeline example.
 *
 * Demonstrates async iteration over streaming responses with
 * SSE formatting and graceful cleanup on disconnection.
 *
 * Run with:
 *   OPENAI_API_KEY=sk-... bun run examples/llmix/typescript/streaming-pipeline.ts
 */

import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
  type CallInput,
  type StreamChunk,
} from "@snoai/llmix";

async function main(): Promise<void> {
  const pipeline = new CallPipeline({
    dispatch: openaiDispatch(),
    responseCache: new TwoTierCache("memory"),
  });
  pipeline.setKeyPool("openai", new KeyPool([process.env["OPENAI_API_KEY"]!]));

  const input: CallInput = {
    config: {
      provider: "openai" as const,
      model: "gpt-4.1-mini",
      common: { temperature: 0.7, maxOutputTokens: 512 },
      caching: { strategy: "memory" as const },
    },
    messages: [
      { role: "system", content: "You are a helpful coding assistant." },
      { role: "user", content: "Explain the observer pattern with a TypeScript example." },
    ],
  };

  // Stream with async iteration
  console.log("=== Streaming response ===\n");
  let tokenCount = 0;
  const startTime = performance.now();
  let ttft: number | null = null;

  for await (const chunk: StreamChunk of pipeline.stream(input)) {
    if (ttft === null) {
      ttft = performance.now() - startTime;
    }
    tokenCount++;
    process.stdout.write(chunk.delta);
  }

  const totalTime = performance.now() - startTime;
  console.log(`\n\n=== Stream complete ===`);
  console.log(`  Time-to-first-token: ${ttft?.toFixed(0)}ms`);
  console.log(`  Total time: ${totalTime.toFixed(0)}ms`);
  console.log(`  Tokens: ${tokenCount}`);

  // SSE formatting helper for HTTP responses
  console.log("\n=== SSE format example ===");
  for await (const chunk of pipeline.stream(input)) {
    // Format as Server-Sent Events
    const sseEvent = `data: ${JSON.stringify({ delta: chunk.delta })}\n\n`;
    process.stdout.write(sseEvent);
    break; // Just show first event as example
  }
  console.log("data: [DONE]\n");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
