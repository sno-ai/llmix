#!/usr/bin/env -S npx tsx
// declare const process: { env: Record<string, string | undefined>; exit(code: number): never; argv: string[] };
// declare const Buffer: { from(str: string): { toString(encoding: string): string } };
// declare function require(module: string): any;
/**
 * Generate llm-pricing.ts from Langfuse Models API (disabled)
 *
 * Usage:
 *   bun pricing:generate
 *   # or: npx tsx scripts/generate-llm-pricing.ts [output-path]
 *
 * Output: writes to ./hrkg-server/lib/llm-pricing/pricing.ts (creates directories if needed)
 *
 * Langfuse pricing generator disabled - Helicone pricing sync is the source of truth.
 */

const fs = require("fs");
const path = require("path");

// const LANGFUSE_BASE_URL = process.env.LANGFUSE_BASE_URL || "https://trace.sno.ai";
// const LANGFUSE_PUBLIC_KEY = process.env.LANGFUSE_PUBLIC_KEY || "pk-lf-cbec71eb-570b-4c28-87c8-ef5d0c418d14";
// const LANGFUSE_SECRET_KEY = process.env.LANGFUSE_SECRET_KEY || "sk-lf-a6c4c2f9-fa45-481f-82c1-e5bd2d6f3b09";
const LANGFUSE_BASE_URL = "";
const LANGFUSE_PUBLIC_KEY = "";
const LANGFUSE_SECRET_KEY = "";

// Output path relative to where script is run
const OUTPUT_PATH = process.argv[2] || "./package/llm-pricing/pricing.ts";

interface LangfuseModel {
  modelName: string;
  inputPrice: number | null;
  outputPrice: number | null;
  unit: string | null;
  isLangfuseManaged: boolean;
}

interface LangfuseResponse {
  data: LangfuseModel[];
  meta?: { totalPages: number };
}

async function fetchModels(): Promise<LangfuseModel[]> {
  const auth = Buffer.from(`${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}`).toString("base64");
  const allModels: LangfuseModel[] = [];
  let page = 1;
  let totalPages = 1;

  do {
    const response = await fetch(`${LANGFUSE_BASE_URL}/api/public/models?limit=100&page=${page}`, {
      headers: { Authorization: `Basic ${auth}` },
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch models: ${response.statusText}`);
    }

    const data = (await response.json()) as LangfuseResponse;
    allModels.push(...data.data);
    totalPages = data.meta?.totalPages || 1;
    page++;
  } while (page <= totalPages);

  return allModels;
}

function generatePricingTs(models: LangfuseModel[]): string {
  // Filter to custom models only (not Langfuse-managed), with pricing
  const pricedModels = models.filter(
    (m) => !m.isLangfuseManaged && (m.inputPrice !== null || m.outputPrice !== null)
  );

  // Use per-token prices directly (same unit as Langfuse)
  // To calculate cost: tokens * price = USD
  const formatPrice = (p: number) => p === 0 ? "0" : p.toFixed(10).replace(/\.?0+$/, "");
  const entries = pricedModels
    .map((m) => {
      const input = formatPrice(m.inputPrice || 0);
      const output = formatPrice(m.outputPrice || 0);
      return `  "${m.modelName}": { input: ${input}, output: ${output} },`;
    })
    .join("\n");

  const timestamp = new Date().toISOString();

  return `/**
 * LLM Model Pricing Table
 *
 * AUTO-GENERATED from Langfuse (${LANGFUSE_BASE_URL})
 * Generated: ${timestamp}
 *
 * DO NOT EDIT MANUALLY - Update in Langfuse, then regenerate:
 *   npx tsx scripts/generate-llm-pricing.ts
 *
 * Pricing: USD per token (multiply by token count directly)
 */

/**
 * Model pricing: USD per token
 * Usage: cost = tokens * price
 * Source: Langfuse (${LANGFUSE_BASE_URL})
 */
export const MODEL_PRICING: Record<string, { input: number; output: number }> = {
${entries}
};

/**
 * Get pricing for a specific model
 */
export function getModelPricing(modelName: string): { input: number; output: number } | null {
  // Try exact match first
  if (MODEL_PRICING[modelName]) {
    return MODEL_PRICING[modelName];
  }

  // Try case-insensitive match
  const lowerName = modelName.toLowerCase();
  for (const [key, value] of Object.entries(MODEL_PRICING)) {
    if (key.toLowerCase() === lowerName) {
      return value;
    }
  }

  console.warn(\`[llm-pricing] No pricing data for model: \${modelName}\`);
  return null;
}

/**
 * Calculate costs for an LLM call
 * Direct multiplication: tokens * price_per_token = cost
 */
export function calculateCost(
  modelName: string,
  promptTokens: number,
  completionTokens: number
): {
  inputCostUsd: number;
  outputCostUsd: number;
  totalCostUsd: number;
} {
  const pricing = getModelPricing(modelName);

  if (!pricing) {
    return { inputCostUsd: 0, outputCostUsd: 0, totalCostUsd: 0 };
  }

  const inputCostUsd = promptTokens * pricing.input;
  const outputCostUsd = completionTokens * pricing.output;
  const totalCostUsd = inputCostUsd + outputCostUsd;

  return {
    inputCostUsd: Number(inputCostUsd.toFixed(6)),
    outputCostUsd: Number(outputCostUsd.toFixed(6)),
    totalCostUsd: Number(totalCostUsd.toFixed(6)),
  };
}
`;
}

async function main() {
  try {
    console.error(
      "Langfuse pricing generator disabled. Use Helicone pricing sync (see package/llmix/src/pricing/)."
    );
    return;

    console.log(`Fetching models from ${LANGFUSE_BASE_URL}...`);
    const models = await fetchModels();
    const customModels = models.filter((m) => !m.isLangfuseManaged);
    console.log(`Found ${models.length} models (${customModels.length} custom)`);

    const output = generatePricingTs(models);

    // Ensure output directory exists
    const dir = path.dirname(OUTPUT_PATH);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.writeFileSync(OUTPUT_PATH, output);
    console.log(`Written to ${OUTPUT_PATH}`);
  } catch (error) {
    console.error("Error generating pricing:", error);
    process.exit(1);
  }
}

main();
