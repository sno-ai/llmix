/**
 * LLM Model Pricing Table
 *
 * AUTO-GENERATED from Helicone API (https://api.helicone.ai)
 * Last synced: 2026-01-29
 *
 * Pricing: USD per 1M tokens (input/output)
 * - For rerankers: input = cost per 1M tokens processed, output = 0
 * - For embeddings: input = cost per 1M tokens, output = 0
 *
 * Note: Date suffixes are stripped automatically in lookups.
 * e.g., "gpt-5-mini-2025-08-07" → "gpt-5-mini"
 *
 * To update: cd ~/infra/onprem-infra && ./scripts/sync-llm-pricing/sync.sh
 */

/**
 * Unified model pricing: USD per 1M tokens
 * Only base model names - date variants resolved via normalization
 */
export const MODEL_PRICING: Record<string, { input: number; output: number }> = {
	// ============================================
	// OpenAI
	// ============================================
	"chatgpt-4o-latest": { input: 5.28, output: 15.82 },
	"codex-mini-latest": { input: 1.5, output: 6 },
	"gpt-4.1": { input: 2, output: 8 },
	"gpt-4.1-mini": { input: 0.4, output: 1.6 },
	"gpt-4.1-nano": { input: 0.1, output: 0.4 },
	"gpt-4o": { input: 2.5, output: 10 },
	"gpt-4o-mini": { input: 0.15, output: 0.6 },
	"gpt-5": { input: 1.25, output: 10 },
	"gpt-5-chat-latest": { input: 1.25, output: 10 },
	"gpt-5-codex": { input: 1.25, output: 10 },
	"gpt-5-mini": { input: 0.25, output: 2 },
	"gpt-5-nano": { input: 0.05, output: 0.4 },
	"gpt-5-pro": { input: 15, output: 120 },
	"gpt-5.1": { input: 1.25, output: 10 },
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
	"text-embedding-3-small": { input: 0.02, output: 0 },
	"text-embedding-3-large": { input: 0.13, output: 0 },

	// ============================================
	// Anthropic
	// ============================================
	"claude-4.5-haiku": { input: 1, output: 5 },
	"claude-4.5-sonnet": { input: 3, output: 15 },

	// ============================================
	// Google
	// ============================================
	"gemini-2.5-flash": { input: 0.3, output: 2.5 },
	"gemini-2.5-flash-lite": { input: 0.1, output: 0.4 },
	"gemini-2.5-pro-preview": { input: 1.25, output: 10 },
	"gemini-3-flash-preview": { input: 0.5, output: 3 },
	"gemini-3-pro-image-preview": { input: 2, output: 12 },
	"gemini-3-pro-preview": { input: 2, output: 12 },
	"gemini-embedding-001": { input: 0, output: 0 },

	// ============================================
	// DeepSeek
	// ============================================
	"deepseek-r1-distill-llama-70b": { input: 0.03, output: 0.13 },
	"deepseek-reasoner": { input: 0.56, output: 1.68 },
	"deepseek-v3": { input: 0.27, output: 1 },
	"deepseek-v3.2": { input: 0.26, output: 0.4 },

	// ============================================
	// Mistral
	// ============================================
	"mistral-large": { input: 2, output: 6 },
	"mistral-nemo": { input: 20, output: 40 },
	"mistral-small": { input: 75, output: 200 },

	// ============================================
	// Other (Gemma, etc.)
	// ============================================
	"gemma-3-12b-it": { input: 0.05, output: 0.1 },
	"gemma2-9b-it": { input: 0.01, output: 0.03 },

	// ============================================
	// Rerankers (input only, output = 0)
	// ============================================
	// Cohere (~$2 per 1K searches ≈ $2 per 1M tokens)
	"rerank-english-v3.5": { input: 2, output: 0 },
	"rerank-multilingual-v3.5": { input: 2, output: 0 },
	"rerank-v3.5": { input: 2, output: 0 },
	// Google Semantic Ranker (free tier)
	"semantic-ranker-default-004": { input: 0, output: 0 },
	"semantic-ranker-default@latest": { input: 0, output: 0 },
	"semantic-ranker-fast-004": { input: 0, output: 0 },

	// ============================================
	// Self-hosted (free - our own GPUs)
	// ============================================
	// Embeddings
	"qwen3-vl-embedding-8b": { input: 0, output: 0 },
	"qwen3-embedding-4b": { input: 0, output: 0 },
	"qwen3-embedding-8b": { input: 0, output: 0 },
	// Rerankers
	"qwen3-reranker-4b": { input: 0, output: 0 },
	"qwen3-reranker-8b": { input: 0, output: 0 },
	"qwen3-vl-reranker-2b": { input: 0, output: 0 },
}

/**
 * Normalize model name for lookup:
 * - Strip date suffixes: -2025-08-07, -20251001, -2411
 * - Handle Anthropic naming: claude-haiku-4-5 → claude-4.5-haiku
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

	// Anthropic naming normalization: claude-haiku-4-5 → claude-4.5-haiku
	// claude-sonnet-4-5 → claude-4.5-sonnet
	const anthropicMatch = normalized.match(/^claude-(haiku|sonnet|opus)-(\d+)-(\d+)$/)
	if (anthropicMatch) {
		const [, tier, major, minor] = anthropicMatch
		normalized = `claude-${major}.${minor}-${tier}`
	}

	return normalized
}

/**
 * Get pricing for a specific model
 * @returns { input, output } in USD per 1M tokens, or null if not found
 *
 * Handles various input formats:
 * - Exact match: "gpt-5-mini"
 * - With date: "gpt-5-mini-2025-08-07" → "gpt-5-mini"
 * - Anthropic: "claude-haiku-4-5-20251001" → "claude-4.5-haiku"
 * - With prefix: "models/gemini-2.5-flash" → "gemini-2.5-flash"
 */
export function getModelPricing(modelName: string): { input: number; output: number } | null {
	// Try exact match first
	if (MODEL_PRICING[modelName]) {
		return MODEL_PRICING[modelName]
	}

	// Try normalized match
	const normalized = normalizeModelName(modelName)
	if (MODEL_PRICING[normalized]) {
		return MODEL_PRICING[normalized]
	}

	// Try lowercase only (for case mismatches without date suffix)
	const lowercase = modelName.toLowerCase()
	if (MODEL_PRICING[lowercase]) {
		return MODEL_PRICING[lowercase]
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
): {
	inputCostUsd: number
	outputCostUsd: number
	totalCostUsd: number
} {
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

// Backwards compatibility - deprecated, use calculateCost instead
export const calculateRerankCost = (modelName: string, searchCount: number = 1): number => {
	// Rough estimate: 1 search ≈ 1000 tokens
	const estimatedTokens = searchCount * 1000
	return calculateCost(modelName, estimatedTokens, 0).totalCostUsd
}
