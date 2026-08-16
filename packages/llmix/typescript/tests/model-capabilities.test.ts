import {
	filterOpenAIProviderOptions,
	getModelCapabilities,
	adjustTemperatureForModel,
} from "../src/model-capabilities.js"
import { getModelPricing } from "../src/pricing/pricing.js"

let passed = 0
let failed = 0

function check(label: string, actual: unknown, expected: unknown): void {
	if (JSON.stringify(actual) === JSON.stringify(expected)) {
		passed++
		console.log(`+ ${label}`)
	} else {
		failed++
		console.log(`x ${label} - expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
	}
}

// A gateway-addressed id must classify exactly like its bare form.
// Regression: `openai/gpt-5.6-luna` classified as `standard`, so reasoningEffort
// was silently deleted before the request and the effort setting did nothing.
console.log("Gateway vendor prefix does not change classification:\n")

for (const [prefixed, bare] of [
	["openai/gpt-5.6-luna", "gpt-5.6-luna"],
	["openai/gpt-5-mini", "gpt-5-mini"],
	["openai/o3-mini", "o3-mini"],
	["z-ai/glm-5.2", "glm-5.2"],
	["deepseek/deepseek-v4-flash-0731", "deepseek-v4-flash-0731"],
] as const) {
	check(`${prefixed} classifies as ${bare}`, getModelCapabilities(prefixed), getModelCapabilities(bare))
}

console.log("\nreasoningEffort survives for reasoning models, prefixed or not:\n")

for (const id of [
	"gpt-5.6-luna",
	"openai/gpt-5.6-luna",
	"glm-5.2",
	"z-ai/glm-5.2",
	"o3-mini",
	"openai/o3-mini",
]) {
	const { filteredOptions } = filterOpenAIProviderOptions(id, { reasoningEffort: "xhigh" })
	check(`${id} keeps xhigh`, filteredOptions?.reasoningEffort, "xhigh")
}

console.log("\nreasoningEffort is still filtered for non-reasoning models:\n")

for (const id of [
	"deepseek/deepseek-v4-flash-0731",
	"deepseek-v4-flash-0731",
	"gpt-4o",
	"openai/gpt-4o",
	"claude-4.5-haiku",
]) {
	const { filteredOptions, filteredParams } = filterOpenAIProviderOptions(id, {
		reasoningEffort: "xhigh",
	})
	check(`${id} drops effort`, filteredOptions?.reasoningEffort, undefined)
	check(`${id} reports what it dropped`, filteredParams.reasoningEffort, "xhigh")
}

// Being a reasoning model and being forbidden to send a temperature are
// different facts. Only the OpenAI families carry the temperature restriction.
console.log("\nFixed temperature applies to the OpenAI families only:\n")

check("gpt-5.6-luna has fixed temperature", getModelCapabilities("gpt-5.6-luna").fixedTemperature, true)
check("o3-mini has fixed temperature", getModelCapabilities("o3-mini").fixedTemperature, true)
check("glm-5.2 is reasoning", getModelCapabilities("glm-5.2").isReasoningModel, true)
check("glm-5.2 keeps its temperature", getModelCapabilities("glm-5.2").fixedTemperature, false)
check("glm-5.2 temperature is not rewritten", adjustTemperatureForModel("z-ai/glm-5.2", 0.3).adjustedTemperature, 0.3)
check("gpt-5.6-luna temperature is rewritten", adjustTemperatureForModel("openai/gpt-5.6-luna", 0.3).adjustedTemperature, 1)

// Pricing shares the same normalization. Regression: enumerating individual
// vendor prefixes lost pricing for every vendor that was not on the list.
console.log("\nPricing resolves through a gateway prefix:\n")

for (const id of [
	"openai/gpt-5.6-luna",
	"z-ai/glm-5.2",
	"deepseek/deepseek-v4-flash-0731",
	"models/gemini-3-flash-preview",
]) {
	check(`${id} has a price`, getModelPricing(id) !== null, true)
}

check("unknown model still returns null", getModelPricing("vendor/nonexistent-model-xyz"), null)

console.log(`\nResult: ${passed} passed, ${failed} failed`)
process.exit(failed > 0 ? 1 : 0)
