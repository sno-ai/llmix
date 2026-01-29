import { getModelPricing, calculateCost, MODEL_PRICING } from "../src/pricing/pricing"

const testCases: [string, boolean][] = [
	// Base models (should work)
	["gpt-5-mini", true],
	["gpt-5", true],
	["claude-4.5-haiku", true],
	["mistral-large", true],

	// With OpenAI date suffix -YYYY-MM-DD
	["gpt-5-mini-2025-08-07", true],
	["gpt-5-2025-08-07", true],
	["gpt-5-pro-2025-10-01", true],
	["gpt-5.1-2025-11-13", true],

	// With Anthropic date suffix -YYYYMMDD
	["claude-haiku-4-5-20251001", true],
	["claude-sonnet-4-5-20250929", true],

	// With Mistral date suffix -YYMM
	["mistral-large-2411", true],

	// With prefix
	["models/gemini-2.5-flash", true],
	["Qwen/Qwen3-Reranker-4B", true],

	// Non-existent (should fail gracefully)
	["nonexistent-model-xyz", false],
]

console.log("Testing getModelPricing normalization:\n")
let passed = 0
let failed = 0

for (const [model, shouldFind] of testCases) {
	const result = getModelPricing(model)
	const found = result !== null
	const ok = found === shouldFind

	if (ok) {
		passed++
		console.log(`+ ${model}`)
	} else {
		failed++
		console.log(`x ${model} - expected ${shouldFind ? "found" : "null"}, got ${found ? "found" : "null"}`)
	}
}

// Test calculateCost
console.log("\nTesting calculateCost:")
const cost = calculateCost("gpt-5-mini", 1000, 500)
if (cost.totalCostUsd > 0) {
	passed++
	console.log(`+ calculateCost returned: ${JSON.stringify(cost)}`)
} else {
	failed++
	console.log(`x calculateCost failed`)
}

// Test MODEL_PRICING is loaded from JSON
console.log("\nTesting MODEL_PRICING from JSON:")
const modelCount = Object.keys(MODEL_PRICING).length
if (modelCount > 0) {
	passed++
	console.log(`+ MODEL_PRICING has ${modelCount} models`)
} else {
	failed++
	console.log(`x MODEL_PRICING is empty`)
}

console.log(`\nResult: ${passed} passed, ${failed} failed`)
process.exit(failed > 0 ? 1 : 0)
