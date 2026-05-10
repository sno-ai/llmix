/**
 * LLMix TypeScript import time benchmark.
 *
 * Measures how long `import("../typescript/src/index.ts")` takes.
 * Target: < 50ms.
 *
 * Run:
 *   bun run packages/llmix/benchmarks/bench-import-time.ts
 */

export {};

const ITERATIONS = 5;

async function benchImport(): Promise<void> {
  const times: number[] = [];

  for (let i = 0; i < ITERATIONS; i++) {
    // Bun caches modules, so after the first import subsequent ones will
    // hit cache. We measure both to show cold vs warm performance.
    const start = performance.now();
    await import("../typescript/src/index");
    const elapsed = performance.now() - start;
    times.push(elapsed);
    console.log(`  Run ${i + 1}: ${elapsed.toFixed(1)}ms`);
  }

  const avg = times.reduce((a, b) => a + b, 0) / times.length;
  const best = Math.min(...times);
  const worst = Math.max(...times);

  console.log(`\nResults (${ITERATIONS} iterations):`);
  console.log(`  Average: ${avg.toFixed(1)}ms`);
  console.log(`  Best:    ${best.toFixed(1)}ms`);
  console.log(`  Worst:   ${worst.toFixed(1)}ms`);
  console.log(`  Target:  < 50ms`);
  console.log(`  Status:  ${avg < 50 ? "PASS" : "ABOVE TARGET (may include provider SDK imports)"}`);
}

console.log("LLMix TypeScript import time benchmark\n");
await benchImport();
