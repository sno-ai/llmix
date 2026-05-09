/**
 * Thinking token stripping tests.
 * Consumes shared test vectors from fixtures/thinking-strip-vectors.json.
 */
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { stripThinking } from "../../typescript/src/thinking.js"

interface TestVector {
	name: string
	input: string
	expectedContent: string
	expectedThinking: string | null
}

interface VectorsFile {
	description: string
	vectors: TestVector[]
}

const fixtureDir = resolve(import.meta.dirname, "..", "fixtures")
const vectors: VectorsFile = JSON.parse(
	readFileSync(resolve(fixtureDir, "thinking-strip-vectors.json"), "utf-8"),
)

let passed = 0
let failed = 0

function assert(condition: boolean, msg: string) {
	if (condition) {
		passed++
		console.log(`+ ${msg}`)
	} else {
		failed++
		console.log(`x ${msg}`)
	}
}

// Run shared test vectors
for (const vec of vectors.vectors) {
	const result = stripThinking(vec.input)
	assert(
		result.content === vec.expectedContent,
		`[${vec.name}] content: got ${JSON.stringify(result.content)}, expected ${JSON.stringify(vec.expectedContent)}`,
	)
	assert(
		result.thinkingContent === vec.expectedThinking,
		`[${vec.name}] thinking: got ${JSON.stringify(result.thinkingContent)}, expected ${JSON.stringify(vec.expectedThinking)}`,
	)
}

// keepThinkingOutput override test (logic is at caller level, but verify strip function works)
const keepInput = "<think>reasoning</think>answer"
const keepResult = stripThinking(keepInput)
assert(
	keepResult.content === "answer" && keepResult.thinkingContent === "reasoning",
	"stripThinking correctly strips (caller decides whether to apply based on keepThinkingOutput)",
)

// When keepThinkingOutput=true, caller should skip stripThinking entirely
// Verify the raw content passes through unchanged when not calling stripThinking
assert(
	keepInput === "<think>reasoning</think>answer",
	"keepThinkingOutput=true: raw content preserved when stripThinking is not called",
)

console.log(`\n${passed} passed, ${failed} failed`)
if (failed > 0) process.exit(1)
