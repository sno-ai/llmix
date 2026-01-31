/**
 * LLM Model Pricing
 *
 * Pricing data loaded from pricing.json (auto-generated from Helicone API)
 * This module provides lookup and cost calculation functions.
 *
 * Pricing: USD per 1M tokens (input/output)
 * - For rerankers: input = cost per 1M tokens processed, output = 0
 * - For embeddings: input = cost per 1M tokens, output = 0
 *
 * Note: Date suffixes are stripped automatically in lookups.
 * e.g., "gpt-5-mini-2025-08-07" -> "gpt-5-mini"
 *
 * To update pricing data: cd ~/infra/onprem-infra && ./scripts/sync-llm-pricing/sync.sh
 */

import pricingData from "./pricing.json"

// ============================================
// Types
// ============================================

/** Price per 1M tokens */
export interface ModelPrice {
	input: number
	output: number
}

/** Cost breakdown from calculateCost */
export interface CostBreakdown {
	inputCostUsd: number
	outputCostUsd: number
	totalCostUsd: number
}

/** Pricing table type */
export type ModelPricingTable = Record<string, ModelPrice>

// ============================================
// Data
// ============================================

/**
 * Unified model pricing: USD per 1M tokens
 * Only base model names - date variants resolved via normalization
 *
 * Categories in pricing.json:
 * - OpenAI (gpt-*, chatgpt-*, codex-*, text-embedding-*)
 * - Anthropic (claude-*)
 * - Google (gemini-*, semantic-ranker-*)
 * - DeepSeek (deepseek-*)
 * - Mistral (mistral-*)
 * - Rerankers (rerank-*)
 * - Self-hosted (qwen3-*) - free, our own GPUs
 */
const rawPricing = pricingData as Record<string, unknown>

// Filter out metadata keys and validate shape (aligned with Python implementation)
export const MODEL_PRICING: ModelPricingTable = Object.fromEntries(
	Object.entries(rawPricing).filter(([key, value]) => {
		return (
			!key.startsWith("_") &&
			typeof value === "object" &&
			value !== null &&
			typeof (value as ModelPrice).input === "number" &&
			typeof (value as ModelPrice).output === "number"
		)
	})
) as ModelPricingTable

/** Safe hasOwn check to avoid prototype-chain access */
const hasOwn = (key: string): boolean => Object.hasOwn(MODEL_PRICING, key)

// ============================================
// Normalization
// ============================================

/**
 * Normalize model name for lookup:
 * - Strip date suffixes: -2025-08-07, -20251001, -2411
 * - Handle Anthropic naming: claude-haiku-4-5 -> claude-4.5-haiku
 * - Handle prefixes: models/, Qwen/
 * - Lowercase
 */
function normalizeModelName(name: string): string {
	let normalized = name.toLowerCase()

	// Remove models/ prefix
	if (normalized.startsWith("models/")) {
		normalized = normalized.slice(7)
	}

	// Remove Qwen/ prefix and normalize
	normalized = normalized.replace(/^qwen\/qwen/, "qwen")

	// Strip date suffixes:
	// -2025-08-07 (OpenAI YYYY-MM-DD)
	// -20251001 (Anthropic YYYYMMDD)
	// -2411 (Mistral YYMM)
	normalized = normalized
		.replace(/-\d{4}-\d{2}-\d{2}$/, "") // YYYY-MM-DD
		.replace(/-\d{8}$/, "") // YYYYMMDD
		.replace(/-\d{4}$/, "") // YYMM (but be careful with model versions like gpt-5.1)

	// Anthropic naming normalization: claude-haiku-4-5 -> claude-4.5-haiku
	// claude-sonnet-4-5 -> claude-4.5-sonnet
	const anthropicMatch = normalized.match(/^claude-(haiku|sonnet|opus)-(\d+)-(\d+)$/)
	if (anthropicMatch) {
		const [, tier, major, minor] = anthropicMatch
		normalized = `claude-${major}.${minor}-${tier}`
	}

	return normalized
}

// ============================================
// Public API
// ============================================

/**
 * Get pricing for a specific model
 * @returns { input, output } in USD per 1M tokens, or null if not found
 *
 * Handles various input formats:
 * - Exact match: "gpt-5-mini"
 * - With date: "gpt-5-mini-2025-08-07" -> "gpt-5-mini"
 * - Anthropic: "claude-haiku-4-5-20251001" -> "claude-4.5-haiku"
 * - With prefix: "models/gemini-2.5-flash" -> "gemini-2.5-flash"
 */
export function getModelPricing(modelName: string): ModelPrice | null {
	// Try exact match first (use hasOwn to avoid prototype-chain access)
	if (hasOwn(modelName)) {
		return MODEL_PRICING[modelName] ?? null
	}

	// Try normalized match
	const normalized = normalizeModelName(modelName)
	if (hasOwn(normalized)) {
		return MODEL_PRICING[normalized] ?? null
	}

	// Try lowercase only (for case mismatches without date suffix)
	const lowercase = modelName.toLowerCase()
	if (hasOwn(lowercase)) {
		return MODEL_PRICING[lowercase] ?? null
	}

	console.warn(`[llmix/pricing] No pricing data for model: ${modelName}`)
	return null
}

/**
 * Calculate costs for an LLM/embedding/reranker call
 *
 * @param modelName - Model identifier (date suffixes stripped automatically)
 * @param inputTokens - Number of input tokens (prompt, documents, etc.)
 * @param outputTokens - Number of output tokens (completion, 0 for embeddings/rerankers)
 * @returns Cost breakdown in USD
 */
export function calculateCost(
	modelName: string,
	inputTokens: number,
	outputTokens: number = 0
): CostBreakdown {
	// Validate inputs to prevent NaN/negative costs corrupting billing
	if (!Number.isFinite(inputTokens) || !Number.isFinite(outputTokens)) {
		throw new RangeError("inputTokens/outputTokens must be finite numbers")
	}
	if (inputTokens < 0 || outputTokens < 0) {
		throw new RangeError("inputTokens/outputTokens must be >= 0")
	}

	const pricing = getModelPricing(modelName)

	if (!pricing) {
		return { inputCostUsd: 0, outputCostUsd: 0, totalCostUsd: 0 }
	}

	const inputCostUsd = (inputTokens / 1_000_000) * pricing.input
	const outputCostUsd = (outputTokens / 1_000_000) * pricing.output
	const totalCostUsd = inputCostUsd + outputCostUsd

	return {
		inputCostUsd: Number(inputCostUsd.toFixed(6)),
		outputCostUsd: Number(outputCostUsd.toFixed(6)),
		totalCostUsd: Number(totalCostUsd.toFixed(6)),
	}
}

/**
 * Calculate rerank cost (backwards compatibility)
 * @deprecated Use calculateCost instead
 *
 * @param modelName - Reranker model identifier
 * @param searchCount - Number of search operations (rough estimate: 1 search ~ 1000 tokens)
 * @returns Cost in USD
 */
export const calculateRerankCost = (modelName: string, searchCount: number = 1): number => {
	// Rough estimate: 1 search ~ 1000 tokens
	const estimatedTokens = searchCount * 1000
	return calculateCost(modelName, estimatedTokens, 0).totalCostUsd
}
