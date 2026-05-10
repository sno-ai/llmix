/**
 * LLMix TypeScript quickstart.
 *
 * Run with:
 *   OPENAI_API_KEY=sk-... bun run examples/llmix/typescript/quickstart.ts
 */

import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
} from "@snoai/llmix";

async function main(): Promise<void> {
  const pipeline = new CallPipeline({
    dispatch: openaiDispatch(),
    responseCache: new TwoTierCache("memory"),
  });
  pipeline.setKeyPool("openai", new KeyPool([process.env["OPENAI_API_KEY"]!]));

  const response = await pipeline.call({
    config: {
      provider: "openai",
      model: "gpt-4o-mini",
      common: { temperature: 0.7, maxOutputTokens: 256 },
      caching: { strategy: "memory" },
    },
    messages: [
      { role: "user", content: "In one sentence, what is LLMix?" },
    ],
  });

  console.log(response.content);
  console.log(`cache_hit=${response.cacheHit} usage=${JSON.stringify(response.usage)}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
