/**
 * LLM Model Pricing Table
 *
 * AUTO-GENERATED from Helicone API (https://api.helicone.ai)
 * Generated: 2026-01-27T09:36:40.403Z
 *
 * DO NOT EDIT MANUALLY - Run sync script to update:
 *   cd ~/infra/onprem-infra && ./scripts/sync-llm-pricing/sync.sh
 *
 * Pricing: USD per 1M tokens
 */

/**
 * Model pricing: USD per 1M tokens
 */
const MODEL_PRICING: Record<string, { input: number; output: number }> = {
	// OpenAI
	"gemini-embedding-001": { input: 0, output: 0 },
	"gpt-4.1-2025-04-14": { input: 2, output: 8 },
	"gpt-4.1-mini-2025-04-14": { input: 0.4, output: 1.6 },
	"gpt-4.1-nano": { input: 0.1, output: 0.4 },
	"gpt-4o": { input: 2.5, output: 10 },
	"gpt-4o-mini": { input: 0.15, output: 0.6 },
	"gpt-5": { input: 1.25, output: 10 },
	"gpt-5-2025-08-07": { input: 1.25, output: 10 },
	"gpt-5-chat-latest": { input: 1.25, output: 10 },
	"gpt-5-codex": { input: 1.25, output: 10 },
	"gpt-5-mini": { input: 0.25, output: 2 },
	"gpt-5-mini-2025-08-07": { input: 0.25, output: 2 },
	"gpt-5-nano": { input: 0.05, output: 0.4 },
	"gpt-5-nano-2025-08-07": { input: 0.05, output: 0.4 },
	"gpt-5-pro": { input: 15, output: 120 },
	"gpt-5-pro-2025-10-01": { input: 15, output: 120 },
	"gpt-5.1": { input: 1.25, output: 10 },
	"gpt-5.1-2025-11-13": { input: 1.25, output: 10 },
	"gpt-5.1-chat-latest": { input: 1.25, output: 10 },
	"gpt-5.1-codex": { input: 1.25, output: 10 },
	"gpt-5.1-codex-mini": { input: 0.25, output: 2 },
	"gpt-5.2": { input: 1.75, output: 14 },
	"gpt-5.2-chat-latest": { input: 1.75, output: 14 },
	"gpt-5.2-pro": { input: 21, output: 168 },
	"gpt-image-1": { input: 6.25, output: 12.5 },
	"gpt-image-1.5": { input: 5, output: 10 },
	"gpt-oss-120b": { input: 0.04, output: 0.16 },
	"gpt-oss-20b": { input: 0.05, output: 0.2 },
	"qwen3-vl-embedding-8b": { input: 0, output: 0 },

	// Anthropic
	"claude-4.5-haiku": { input: 1, output: 5 },
	"claude-4.5-sonnet": { input: 3, output: 15 },
	"claude-haiku-4-5-20251001": { input: 1, output: 5 },
	"claude-sonnet-4-5-20250929": { input: 3, output: 15 },

	// Google
	"gemini-2.0-flash-exp": { input: 0, output: 0 },
	"gemini-2.5-flash-lite": { input: 0.1, output: 0.4 },
	"gemini-2.5-pro-preview": { input: 1.25, output: 10 },
	"gemini-3-flash-preview": { input: 0.5, output: 3 },
	"gemini-3-pro-image-preview": { input: 2, output: 12 },
	"gemini-3-pro-preview": { input: 2, output: 12 },
	"models/gemini-2.5-flash": { input: 0.3, output: 2.5 },
	"semantic-ranker-default-004": { input: 0, output: 0 },
	"semantic-ranker-default@latest": { input: 0, output: 0 },
	"semantic-ranker-fast-004": { input: 0, output: 0 },

	// Other (Jina, self-hosted, etc.)
	"Qwen/Qwen3-Reranker-4B": { input: 0, output: 0 },
	"Qwen/Qwen3-Reranker-8B": { input: 0, output: 0 },
	"chatgpt-4o-latest": { input: 5.28, output: 15.82 },
	"codex-mini-latest": { input: 1.5, output: 6 },
	"deepseek-r1-distill-llama-70b": { input: 0.03, output: 0.13 },
	"deepseek-reasoner": { input: 0.56, output: 1.68 },
	"deepseek-tng-r1t2-chimera": { input: 0.3, output: 1.2 },
	"deepseek-v3": { input: 0.27, output: 1 },
	"deepseek-v3.1-terminus": { input: 0.27, output: 1 },
	"deepseek-v3.2": { input: 0.26, output: 0.4 },
	"gemma-3-12b-it": { input: 0.05, output: 0.1 },
	"gemma2-9b-it": { input: 0.01, output: 0.03 },
	"mistral-large-2411": { input: 2, output: 6 },
	"mistral-nemo": { input: 20, output: 40 },
	"mistral-small": { input: 75, output: 200 },
	"qwen3-vl-reranker-2b": { input: 0, output: 0 },
}

/**
 * Cohere Rerank Pricing: USD per 1,000 searches
 * A "search" = 1 query against a document set
 * https://cohere.com/pricing
 */
const RERANK_PRICING: Record<string, number> = {
	"rerank-english-v3.5": 2,
	"rerank-multilingual-v3.5": 2,
	"rerank-v3.5": 2,
}

/**
 * Calculate cost for a Cohere rerank call
 *
 * @param modelName - Rerank model identifier
 * @param searchCount - Number of searches (usually 1 per rerank call)
 * @returns Cost in USD
 */
export function calculateRerankCost(modelName: string, searchCount: number = 1): number {
	const pricePerThousand = RERANK_PRICING[modelName]
	if (!pricePerThousand) {
		console.warn(`[llmix/pricing] No pricing data for rerank model: ${modelName}`)
		return 0
	}
	return Number(((searchCount / 1000) * pricePerThousand).toFixed(6))
}

/**
 * Get pricing for a specific model
 */
export function getModelPricing(modelName: string): { input: number; output: number } | null {
	const pricing = MODEL_PRICING[modelName]
	if (!pricing) {
		console.warn(`[llmix/pricing] No pricing data for model: ${modelName}`)
		return null
	}
	return pricing
}

/**
 * Calculate costs for an LLM call
 *
 * @param modelName - Model identifier
 * @param promptTokens - Number of input tokens
 * @param completionTokens - Number of output tokens
 * @returns Cost breakdown in USD
 */
export function calculateCost(
	modelName: string,
	promptTokens: number,
	completionTokens: number
): {
	inputCostUsd: number
	outputCostUsd: number
	totalCostUsd: number
} {
	const pricing = getModelPricing(modelName)

	if (!pricing) {
		// Unknown model - return zero costs
		return {
			inputCostUsd: 0,
			outputCostUsd: 0,
			totalCostUsd: 0,
		}
	}

	// Calculate costs: (tokens / 1M) * price_per_1M
	const inputCostUsd = (promptTokens / 1_000_000) * pricing.input
	const outputCostUsd = (completionTokens / 1_000_000) * pricing.output
	const totalCostUsd = inputCostUsd + outputCostUsd

	return {
		inputCostUsd: Number(inputCostUsd.toFixed(6)),
		outputCostUsd: Number(outputCostUsd.toFixed(6)),
		totalCostUsd: Number(totalCostUsd.toFixed(6)),
	}
}
