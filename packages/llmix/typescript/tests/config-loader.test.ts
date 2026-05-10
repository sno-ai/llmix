import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

import { loadMdaConfig, loadMdaConfigFromFile, loadMdaConfigPreset, resolveConfigDir } from "../src/config.js"
import { InvalidConfigError, SecurityError, VALID_PROVIDERS } from "../src/types.js"
import { ProviderSchema } from "../src/mda-loader.js"

const fixtureDir = resolve(import.meta.dirname, "..", "..", "..", "..", "fixtures", "mda", "valid")
const presetPath = resolve(fixtureDir, "sample_preset.mda")

let passed = 0
let failed = 0

async function run(name: string, fn: () => Promise<void>): Promise<void> {
	try {
		await fn()
		passed++
		console.log(`[PASS] ${name}`)
	} catch (error) {
		failed++
		console.log(`[FAIL] ${name}: ${error instanceof Error ? error.stack ?? error.message : String(error)}`)
	}
}

function mdaSource(frontmatter: string): string {
	return `---\n${frontmatter.trim()}\n---\n\n# Test preset\n`
}

function writeMda(tempDir: string, name: string, frontmatter: string): string {
	const filePath = join(tempDir, name)
	writeFileSync(filePath, mdaSource(frontmatter), "utf-8")
	return filePath
}

await run("loadMdaConfig projects fixture namespace into LLMConfig", async () => {
	const loadedFromPath = await loadMdaConfig(presetPath)

	assert.equal(loadedFromPath.provider, "openai")
	assert.equal(loadedFromPath.model, "gpt-5-mini")
	assert.equal(loadedFromPath.common?.temperature, 0.7)
	assert.equal(loadedFromPath.common?.maxOutputTokens, 4096)
	assert.equal("provider" in (loadedFromPath.common ?? {}), false)
	assert.equal("model" in (loadedFromPath.common ?? {}), false)
	assert.equal(loadedFromPath.providerOptions?.openai?.reasoningEffort, "medium")
	assert.ok(loadedFromPath.description?.startsWith("Fast cheap multi-tool calls"))
	assert.equal(loadedFromPath.caching?.strategy, "memory")
	assert.equal("tags" in loadedFromPath, false)
})

await run("VALID_PROVIDERS matches the provider schema", async () => {
	assert.deepEqual([...VALID_PROVIDERS], ProviderSchema.options)
})

await run("loadMdaConfigPreset resolves only .mda preset files", async () => {
	const loadedFromPreset = await loadMdaConfigPreset("sample_preset", fixtureDir)
	assert.equal(loadedFromPreset.provider, "openai")
	assert.equal(loadedFromPreset.model, "gpt-5-mini")
})

await run("direct .mda load preserves camelCase runtime shape", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const directConfigPath = writeMda(
		tempDir,
		"compat.mda",
		`
name: compat
description: Compatibility shape test.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
      maxOutputTokens: 123
      keepThinkingOutput: true
    providerOptions:
      openai:
        reasoningEffort: high
        logitBias:
          123: -5
      deepinfra:
        provider: deepinfra
      novita:
        provider: novita
      together:
        provider: together
    caching:
      strategy: memory
      maxItems: 99
`,
	)

	const normalized = await loadMdaConfig(directConfigPath)
	assert.equal(normalized.common?.maxOutputTokens, 123)
	assert.equal(normalized.common?.keepThinkingOutput, true)
	assert.equal(normalized.providerOptions?.openai?.reasoningEffort, "high")
	assert.equal(normalized.providerOptions?.openai?.logitBias?.["123"], -5)
	assert.equal(normalized.providerOptions?.deepinfra?.["provider"], "deepinfra")
	assert.equal(normalized.providerOptions?.novita?.["provider"], "novita")
	assert.equal(normalized.providerOptions?.together?.["provider"], "together")
	assert.equal(normalized.caching?.maxItems, 99)
})

await run("MDA presets load exported OpenAI-compatible providers", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))

	for (const provider of ["deepinfra", "novita", "together"] as const) {
		const normalized = await loadMdaConfig(
			writeMda(
				tempDir,
				`${provider}.mda`,
				`
name: ${provider}
description: ${provider} provider test.
metadata:
  snoai-llmix:
    common:
      provider: ${provider}
      model: test-model
    providerOptions:
      ${provider}:
        provider: ${provider}
`,
			),
		)

		assert.equal(normalized.provider, provider)
		assert.equal(normalized.providerOptions?.[provider]?.["provider"], provider)
	}
})

await run("minimal MDA preset projects required fields with safe defaults", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const normalized = await loadMdaConfig(
		writeMda(
			tempDir,
			"minimal.mda",
			`
name: minimal
description: Top-level description fallback.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
		),
	)

	assert.equal(normalized.provider, "openai")
	assert.equal(normalized.model, "gpt-4.1-mini")
	assert.equal(normalized.description, "Top-level description fallback.")
	assert.equal(normalized.common, undefined)
	assert.equal(normalized.providerOptions, undefined)
	assert.equal(normalized.timeout, undefined)
	assert.equal(normalized.tags, undefined)
	assert.equal(normalized.caching, undefined)
	assert.equal(normalized.bypassGateway, undefined)
})

await run("LLMix namespace description and tags override top-level MDA values", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const normalized = await loadMdaConfig(
		writeMda(
			tempDir,
			"namespace-overrides.mda",
			`
name: namespace-overrides
description: Top-level description.
tags:
  - top-level
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
    description: Namespace description.
    tags:
      - namespace
`,
		),
	)

	assert.equal(normalized.description, "Namespace description.")
	assert.deepEqual(normalized.tags, ["namespace"])
})

await run("MDA metadata allows non-LLMix namespaces while keeping LLMix strict", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const normalized = await loadMdaConfig(
		writeMda(
			tempDir,
			"vendor-metadata.mda",
			`
name: vendor-metadata
description: Vendor metadata test.
metadata:
  mda:
    schema-version: 1
  other-vendor:
    owner: platform
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
		),
	)

	assert.equal(normalized.provider, "openai")
	assert.equal(normalized.model, "gpt-4.1-mini")
})

await run("projected runtime validation rejects invalid values", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))

	await assert.rejects(
		loadMdaConfig(
			writeMda(
				tempDir,
				"bad-temperature.mda",
				`
name: bad-temperature
description: Invalid temperature.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
      temperature: 3
`,
			),
		),
		Error,
	)

	await assert.rejects(
		loadMdaConfig(
			writeMda(
				tempDir,
				"bad-anthropic-thinking.mda",
				`
name: bad-anthropic-thinking
description: Invalid Anthropic thinking budget.
metadata:
  snoai-llmix:
    common:
      provider: anthropic
      model: claude-test
    providerOptions:
      anthropic:
        thinking:
          type: enabled
          budgetTokens: 512
`,
			),
		),
		Error,
	)
})

await run("mixed legacy bypassGateway and caching settings remain loadable", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const normalized = await loadMdaConfig(
		writeMda(
			tempDir,
			"mixed-cache.mda",
			`
name: mixed-cache
description: Mixed cache migration fields.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
    caching:
      strategy: native
    bypassGateway: true
`,
		),
	)

	assert.equal(normalized.caching?.strategy, "native")
	assert.equal(normalized.bypassGateway, true)
})

await run("module preset loader resolves MDA files and rejects traversal inputs", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const moduleDir = join(tempDir, "search")
	mkdirSync(moduleDir, { recursive: true })
	writeMda(
		moduleDir,
		"summary.mda",
		`
name: summary
description: Module preset path test.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
	)

	const normalized = await loadMdaConfigFromFile(tempDir, "search", "summary")
	assert.equal(normalized.model, "gpt-4.1-mini")
	await assert.rejects(loadMdaConfigFromFile(tempDir, "../search", "summary"), SecurityError)
	await assert.rejects(loadMdaConfigFromFile(tempDir, "search", "../summary"), SecurityError)
})

await run("symlinked MDA file cannot escape the requested directory", async () => {
	const tempRoot = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const insideDir = join(tempRoot, "inside")
	const outsideDir = join(tempRoot, "outside")
	mkdirSync(insideDir, { recursive: true })
	mkdirSync(outsideDir, { recursive: true })
	const outsidePath = writeMda(
		outsideDir,
		"escape.mda",
		`
name: escape
description: Escaped symlink target.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
	)
	const symlinkPath = join(insideDir, "escape.mda")
	symlinkSync(outsidePath, symlinkPath)

	await assert.rejects(loadMdaConfig(symlinkPath), SecurityError)
})

await run("loadMdaConfigPreset accepts arbitrary preset directories", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const presetDir = join(tempDir, "llm-presets")
	mkdirSync(presetDir, { recursive: true })
	writeMda(
		presetDir,
		"compat.mda",
		`
name: compat
description: Hyphenated directory test.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
	)

	const normalized = await loadMdaConfigPreset("compat", presetDir)
	assert.equal(normalized.model, "gpt-4.1-mini")
})

await run("resolveConfigDir uses projectRoot for relative env overrides", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const previous = process.env["LLMIX_CONFIG_DIR"]

	try {
		process.env["LLMIX_CONFIG_DIR"] = "custom/llm"
		const resolved = resolveConfigDir({ projectRoot: tempDir })

		assert.equal(resolved.source, "env")
		assert.equal(resolved.configDir, resolve(tempDir, "custom/llm"))
	} finally {
		if (previous === undefined) {
			delete process.env["LLMIX_CONFIG_DIR"]
		} else {
			process.env["LLMIX_CONFIG_DIR"] = previous
		}
	}
})

await run("YAML preset paths are rejected clearly", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const yamlPath = join(tempDir, "compat.yaml")
	const ymlPath = join(tempDir, "compat.yml")

	await assert.rejects(loadMdaConfig(yamlPath), InvalidConfigError)
	await assert.rejects(loadMdaConfigPreset("compat.yml", tempDir), InvalidConfigError)
	await assert.rejects(loadMdaConfigPreset(ymlPath, tempDir), InvalidConfigError)
})

await run("required MDA and LLMix namespace fields are enforced", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))

	await assert.rejects(
		loadMdaConfig(
			writeMda(
				tempDir,
				"missing-name.mda",
				`
description: Missing name.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
			),
		),
		Error,
	)

	await assert.rejects(
		loadMdaConfig(
			writeMda(
				tempDir,
				"missing-description.mda",
				`
name: missing-description
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
			),
		),
		Error,
	)

	await assert.rejects(
		loadMdaConfig(
			writeMda(
				tempDir,
				"missing-namespace.mda",
				`
name: missing-namespace
description: Missing namespace.
metadata: {}
`,
			),
		),
		Error,
	)

	await assert.rejects(
		loadMdaConfig(
			writeMda(
				tempDir,
				"unknown-namespace-field.mda",
				`
name: unknown-namespace-field
description: Unknown namespace field.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
    unsupported: true
`,
			),
		),
		Error,
	)
})

await run("verification options are opt-in and surfaced from MDA package", async () => {
	const tempDir = mkdtempSync(join(tmpdir(), "llmix-config-"))
	const requiresPath = writeMda(
		tempDir,
		"requires-public.mda",
		`
name: requires-public
description: Requires public network.
requires:
  network: public
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
	)

	assert.equal((await loadMdaConfig(requiresPath)).model, "gpt-4.1-mini")
	await assert.rejects(loadMdaConfig(requiresPath, { enforceRequires: true, allowedNetworks: [] }), Error)
	assert.equal((await loadMdaConfig(requiresPath, { enforceRequires: true, allowedNetworks: ["*"] })).provider, "openai")

	const badDigest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
	const integrityPath = writeMda(
		tempDir,
		"bad-integrity.mda",
		`
name: bad-integrity
description: Bad integrity.
integrity:
  algorithm: sha256
  digest: ${badDigest}
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
	)
	await assert.rejects(loadMdaConfig(integrityPath, { verifyIntegrity: true }), Error)

	const signaturePath = writeMda(
		tempDir,
		"signature-policy.mda",
		`
name: signature-policy
description: Signature policy.
integrity:
  algorithm: sha256
  digest: ${badDigest}
signatures:
  - signer: sigstore-oidc:https://issuer.example
    key-id: test-key
    payload-digest: ${badDigest}
    algorithm: ed25519
    signature: test-signature
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
`,
	)
	await assert.rejects(loadMdaConfig(signaturePath, { verifySignatures: true }), Error)
})

console.log(`\n${"=".repeat(40)}`)
console.log(`Results: ${passed} passed, ${failed} failed`)
if (failed > 0) {
	process.exit(1)
}
console.log("All tests passed!")
