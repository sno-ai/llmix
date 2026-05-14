import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdtemp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import {
	ConfigNotFoundError,
	ConfigRegistryManager,
	ConfigRegistryPublisher,
	InvalidConfigError,
	loadMdaConfig,
} from "../src/index.js"
import { compiledRegistryPath, compiledRelativePath } from "../src/config-registry-common.js"

let passed = 0
let failed = 0

function sha256Text(content: string): string {
	return createHash("sha256").update(content).digest("hex")
}

async function withTempRoot(name: string, fn: (root: string) => Promise<void>): Promise<void> {
	const tempRoot = await mkdtemp(path.join(tmpdir(), `llmix-${name}-`))
	const root = path.join(tempRoot, "config", "llm")
	try {
		await fn(root)
	} finally {
		await rm(tempRoot, { recursive: true, force: true })
	}
}

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

async function writeSourcePreset(
	root: string,
	moduleName: string,
	presetName: string,
	options?: {
		provider?: string
		model?: string
		temperature?: number
		maxOutputTokens?: number
		reasoningEffort?: string
		providerOptionsLines?: string[]
	},
): Promise<void> {
	const provider = options?.provider ?? "openai"
	const model = options?.model ?? "gpt-4.1-mini"
	const temperature = options?.temperature ?? 0.2
	const maxOutputTokens = options?.maxOutputTokens ?? 256
	const reasoningEffort = options?.reasoningEffort ?? "medium"
	const providerOptionsLines = options?.providerOptionsLines ?? [
		"      openai:",
		`        reasoningEffort: ${reasoningEffort}`,
	]

	const filePath = path.join(root, "source", moduleName, `${presetName}.mda`)
	await mkdir(path.dirname(filePath), { recursive: true })
	await writeFile(
		filePath,
		[
			"---",
			`name: ${presetName}`,
			`description: ${moduleName}/${presetName} registry test preset.`,
			"metadata:",
			"  snoai-llmix:",
			"    common:",
			`      provider: ${provider}`,
			`      model: ${model}`,
			`      temperature: ${temperature}`,
			`      maxOutputTokens: ${maxOutputTokens}`,
			"    providerOptions:",
			...providerOptionsLines,
			"---",
			"",
			"# Registry test preset",
			"",
		].join("\n"),
		"utf-8",
	)
}

type TestManifestPreset = {
	source_path: string
	source_sha256: string
	resolved_path: string
	resolved_sha256: string
}

type TestManifest = {
	presets: Record<string, TestManifestPreset>
}

async function rewriteManifest(
	root: string,
	revision: string,
	mutate: (manifest: TestManifest) => void,
): Promise<void> {
	const manifestPath = path.join(root, "compiled", revision, "manifest.json")
	const manifest = JSON.parse(await readFile(manifestPath, "utf-8")) as TestManifest
	mutate(manifest)
	const manifestContent = `${JSON.stringify(manifest, null, 2)}\n`
	await writeFile(manifestPath, manifestContent, "utf-8")
	await writeFile(
		path.join(root, "current.json"),
		`${JSON.stringify({ revision, manifest_sha256: sha256Text(manifestContent) })}\n`,
		"utf-8",
	)
}

await run("publish creates active revision and manager reads canonical resolved JSON", async () => {
	await withTempRoot("publish", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-5-mini",
			temperature: 0.7,
			maxOutputTokens: 1024,
			reasoningEffort: "high",
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)
		const config = await manager.getPreset("search", "summary")
		const resolvedPath = path.join(root, "compiled", published.revision, "resolved", "search", "summary.json")
		const sourcePath = path.join(root, "compiled", published.revision, "source", "search", "summary.mda")
		const legacySourcePath = path.join(root, "compiled", published.revision, "source", "search", "summary.yaml")
		const resolved = JSON.parse(await readFile(resolvedPath, "utf-8")) as Record<string, unknown>
		const source = await readFile(sourcePath, "utf-8")
		const resolvedFromCompiledSource = await loadMdaConfig(sourcePath)
		const common = resolved["common"] as Record<string, unknown>
		const providerOptions = resolved["providerOptions"] as Record<string, unknown>
		const openai = providerOptions["openai"] as Record<string, unknown>

		assert.equal(published.activated, true)
		assert.equal(manager.activeRevision, published.revision)
		assert.deepEqual(await manager.availablePresets(), ["search/summary"])
		assert.equal(config.provider, "openai")
		assert.equal(config.model, "gpt-5-mini")
		assert.equal(config.common?.maxOutputTokens, 1024)
		assert.equal(config.providerOptions?.openai?.reasoningEffort, "high")
		assert.deepEqual(resolved, JSON.parse(JSON.stringify(resolvedFromCompiledSource)))
		assert.ok(manager.lastSuccessfulReloadAt instanceof Date)
		assert.equal(manager.lastReloadFailureAt, null)
		assert.equal(common["maxOutputTokens"], 1024)
		assert.equal(openai["reasoningEffort"], "high")
		assert.match(source, /^---\nname: summary/m)
		await assert.rejects(readFile(legacySourcePath, "utf-8"), Error)
	})
})

await run("publish and reload preserve provider option field names", async () => {
	await withTempRoot("provider-options", async (root) => {
		await writeSourcePreset(root, "search", "providers", {
			providerOptionsLines: [
				"      openai:",
				"        maxCompletionTokens: 321",
				"        reasoningEffort: high",
				"      anthropic:",
				"        cacheControl:",
				"          type: ephemeral",
				"          ttl: 1h",
				"        thinking:",
				"          type: enabled",
				"          budgetTokens: 1024",
				"      google:",
				"        thinkingConfig:",
				"          thinkingLevel: high",
				"          thinkingBudget: 2048",
				"          includeThoughts: true",
				"      deepseek:",
				"        thinking:",
				"          type: enabled",
				"      openrouter:",
				"        reasoning:",
				"          effort: high",
				"      sno-gpu:",
				"        enableThinking: true",
				"        thinkingBudget: 4096",
				"      deepinfra:",
				"        provider: deepinfra",
				"      novita:",
				"        provider: novita",
				"      together:",
				"        provider: together",
			],
		})

		await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)
		const config = await manager.getPreset("search", "providers")

		assert.equal(config.providerOptions?.openai?.maxCompletionTokens, 321)
		assert.equal(config.providerOptions?.anthropic?.cacheControl?.ttl, "1h")
		assert.equal(config.providerOptions?.google?.thinkingConfig?.thinkingBudget, 2048)
		assert.equal(config.providerOptions?.deepseek?.thinking?.type, "enabled")
		assert.equal(config.providerOptions?.openrouter?.reasoning?.["effort"], "high")
		assert.equal(config.providerOptions?.["sno-gpu"]?.thinkingBudget, 4096)
		assert.equal(config.providerOptions?.deepinfra?.["provider"], "deepinfra")
		assert.equal(config.providerOptions?.novita?.["provider"], "novita")
		assert.equal(config.providerOptions?.together?.["provider"], "together")
	})
})

await run("manager reloads after current revision changes", async () => {
	await withTempRoot("reload", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
			reasoningEffort: "medium",
		})

		const first = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)

		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-5-mini",
			maxOutputTokens: 2048,
			reasoningEffort: "high",
		})

		const second = await new ConfigRegistryPublisher(root).publish()
		const config = await manager.getPreset("search", "summary")

		assert.notEqual(first.revision, second.revision)
		assert.equal(manager.activeRevision, second.revision)
		assert.equal(config.model, "gpt-5-mini")
		assert.equal(config.common?.maxOutputTokens, 2048)
		assert.equal(config.providerOptions?.openai?.reasoningEffort, "high")
	})
})

await run("manager rolls back when current revision points to an older compiled revision", async () => {
	await withTempRoot("rollback", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
			reasoningEffort: "medium",
		})

		const first = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)

		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-5-mini",
			maxOutputTokens: 2048,
			reasoningEffort: "high",
		})

		const second = await new ConfigRegistryPublisher(root).publish()
		assert.notEqual(first.revision, second.revision)
		assert.equal((await manager.getPreset("search", "summary")).model, "gpt-5-mini")

		await writeFile(
			path.join(root, "current.json"),
			JSON.stringify({ revision: first.revision, manifest_sha256: first.manifestSha256 }) + "\n",
			"utf-8",
		)
		const config = await manager.getPreset("search", "summary")

		assert.equal(manager.activeRevision, first.revision)
		assert.equal(config.model, "gpt-4.1-mini")
		assert.equal(config.common?.maxOutputTokens, 256)
		assert.equal(config.providerOptions?.openai?.reasoningEffort, "medium")
	})
})

await run("available presets refresh after current revision changes", async () => {
	await withTempRoot("available-refresh", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)
		assert.deepEqual(await manager.availablePresets(), ["search/summary"])

		await writeSourcePreset(root, "chat", "reply", {
			model: "gpt-5-mini",
			maxOutputTokens: 512,
		})

		await new ConfigRegistryPublisher(root).publish()

		assert.deepEqual(await manager.availablePresets(), ["chat/reply", "search/summary"])
	})
})

await run("manager ignores source edits until a new revision is published", async () => {
	await withTempRoot("source-edits", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
			reasoningEffort: "medium",
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)

		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-5-mini",
			maxOutputTokens: 2048,
			reasoningEffort: "high",
		})

		const config = await manager.getPreset("search", "summary")

		assert.equal(manager.activeRevision, published.revision)
		assert.equal(config.model, "gpt-4.1-mini")
		assert.equal(config.common?.maxOutputTokens, 256)
		assert.equal(config.providerOptions?.openai?.reasoningEffort, "medium")
	})
})

await run("manager fails fast without an active revision", async () => {
	await withTempRoot("missing-current", async (root) => {
		await mkdir(root, { recursive: true })
		await assert.rejects(ConfigRegistryManager.open(root), ConfigNotFoundError)
	})
})

await run("manager fails fast with a malformed current pointer", async () => {
	await withTempRoot("malformed-current", async (root) => {
		await mkdir(root, { recursive: true })
		await writeFile(path.join(root, "current.json"), '{"revision":42}\n', "utf-8")
		await assert.rejects(ConfigRegistryManager.open(root), InvalidConfigError)
	})
})

await run("manager opens legacy current pointer without manifest hash", async () => {
	await withTempRoot("legacy-current-pointer", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		await writeFile(path.join(root, "current.json"), JSON.stringify({ revision: published.revision }) + "\n", "utf-8")

		const manager = await ConfigRegistryManager.open(root)
		const config = await manager.getPreset("search", "summary")

		assert.equal(manager.activeRevision, published.revision)
		assert.equal(config.model, "gpt-4.1-mini")
		assert.equal(manager.lastReloadError, null)
	})
})

await run("manager keeps last known good config when pointer changes to missing revision", async () => {
	await withTempRoot("missing-revision", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)

		await writeFile(
			path.join(root, "current.json"),
			'{"revision":"missing-revision","manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}\n',
			"utf-8",
		)
		const config = await manager.getPreset("search", "summary")

		assert.equal(manager.activeRevision, published.revision)
		assert.equal(config.model, "gpt-4.1-mini")
		assert.ok(manager.lastReloadError instanceof ConfigNotFoundError)
		assert.ok(manager.lastSuccessfulReloadAt instanceof Date)
		assert.ok(manager.lastReloadFailureAt instanceof Date)
	})
})

await run("manager keeps last known good config when current pointer becomes malformed", async () => {
	await withTempRoot("malformed-current-after-open", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)

		await writeFile(path.join(root, "current.json"), '{"revision":42}\n', "utf-8")
		const config = await manager.getPreset("search", "summary")

		assert.equal(manager.activeRevision, published.revision)
		assert.equal(config.model, "gpt-4.1-mini")
		assert.ok(manager.lastReloadError instanceof InvalidConfigError)
		assert.ok(manager.lastSuccessfulReloadAt instanceof Date)
		assert.ok(manager.lastReloadFailureAt instanceof Date)
	})
})

await run("manager validates manifest hash changes for the active revision", async () => {
	await withTempRoot("same-revision-hash-change", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const manager = await ConfigRegistryManager.open(root)

		await writeFile(
			path.join(root, "current.json"),
			`{"revision":"${published.revision}","manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}\n`,
			"utf-8",
		)
		const config = await manager.getPreset("search", "summary")

		assert.equal(manager.activeRevision, published.revision)
		assert.equal(config.model, "gpt-4.1-mini")
		assert.ok(manager.lastReloadError instanceof InvalidConfigError)
		assert.ok(manager.lastReloadFailureAt instanceof Date)
	})
})

await run("compiledRelativePath requires an exact revision path boundary", async () => {
	assert.equal(compiledRegistryPath("v1", "resolved/search/summary.json"), "compiled/v1/resolved/search/summary.json")
	assert.equal(compiledRelativePath("v1", "compiled/v1/manifest.json"), "manifest.json")
	assert.throws(() => compiledRelativePath("v1", "compiled/v10/manifest.json"), /outside the active compiled revision/)
	assert.throws(() => compiledRelativePath("v1", "compiled/v1"), /outside the active compiled revision/)
	assert.throws(() => compiledRegistryPath("v1", "../current.json"), /traversal segments/)
	assert.throws(() => compiledRegistryPath("v1", "/tmp/current.json"), /relative POSIX path/)
	assert.throws(() => compiledRegistryPath("v1", "source\\search.mda"), /relative POSIX path/)
})

await run("manager rejects malformed manifest artifact digest on startup", async () => {
	await withTempRoot("manifest-bad-digest", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		await rewriteManifest(root, published.revision, (manifest) => {
			const entry = manifest.presets["search/summary"]
			assert.ok(entry)
			entry.resolved_sha256 = "not-a-sha256"
		})

		await assert.rejects(ConfigRegistryManager.open(root), /Invalid SHA-256 digest/)
	})
})

await run("manager rejects manifest artifact traversal path on startup", async () => {
	await withTempRoot("manifest-traversal", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		await rewriteManifest(root, published.revision, (manifest) => {
			const entry = manifest.presets["search/summary"]
			assert.ok(entry)
			entry.resolved_path = "../outside.json"
		})

		await assert.rejects(ConfigRegistryManager.open(root), /traversal segments/)
	})
})

await run("publish retry activates an existing matching compiled revision after current write failure", async () => {
	await withTempRoot("publish-activation-retry", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})
		await mkdir(path.join(root, "current.json"), { recursive: true })

		await assert.rejects(new ConfigRegistryPublisher(root).publish({ revision: "activation_retry" }), Error)
		await rm(path.join(root, "current.json"), { recursive: true, force: true })
		const published = await new ConfigRegistryPublisher(root).publish({ revision: "activation_retry" })
		const manager = await ConfigRegistryManager.open(root)

		assert.equal(published.activated, true)
		assert.equal(manager.activeRevision, "activation_retry")
		assert.equal((await manager.getPreset("search", "summary")).model, "gpt-4.1-mini")
		assert.deepEqual(await readdir(path.join(root, "compiled", ".staging")).catch(() => []), [])
	})
})

await run("publish failure leaves the active revision unchanged", async () => {
	await withTempRoot("publish-failure", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
			reasoningEffort: "medium",
		})

		const first = await new ConfigRegistryPublisher(root).publish()
		await writeFile(
			path.join(root, "source", "search", "summary.mda"),
			"---\nname: broken\ndescription: Broken.\nmetadata:\n  snoai-llmix:\n    common:\n      provider: openai\n      model: [broken\n---\n",
			"utf-8",
		)

		await assert.rejects(new ConfigRegistryPublisher(root).publish(), Error)

		const pointer = JSON.parse(await readFile(path.join(root, "current.json"), "utf-8")) as { revision: string }
		const stagingEntries = await readdir(path.join(root, "compiled", ".staging")).catch(() => [])

		assert.equal(pointer.revision, first.revision)
		assert.deepEqual(stagingEntries, [])
	})
})

await run("legacy YAML source blocks publish without changing active revision", async () => {
	await withTempRoot("legacy-yaml-source", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const first = await new ConfigRegistryPublisher(root).publish()
		await writeFile(path.join(root, "source", "search", "legacy.yaml"), "provider: openai\nmodel: gpt-4.1-mini\n", "utf-8")

		await assert.rejects(new ConfigRegistryPublisher(root).publish(), InvalidConfigError)

		const pointer = JSON.parse(await readFile(path.join(root, "current.json"), "utf-8")) as { revision: string }
		const stagingEntries = await readdir(path.join(root, "compiled", ".staging")).catch(() => [])

		assert.equal(pointer.revision, first.revision)
		assert.deepEqual(stagingEntries, [])
	})
})

await run("parallel publishes use isolated staging and current pointer writes", async () => {
	await withTempRoot("parallel-publish", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const publisher = new ConfigRegistryPublisher(root)
		const [first, second] = await Promise.all([
			publisher.publish({ revision: "parallel_a", activate: true }),
			publisher.publish({ revision: "parallel_b", activate: true }),
		])
		const manager = await ConfigRegistryManager.open(root)
		const stagingEntries = await readdir(path.join(root, "compiled", ".staging")).catch(() => [])

		assert.deepEqual(new Set([first.revision, second.revision]), new Set(["parallel_a", "parallel_b"]))
		assert.ok([first.revision, second.revision].includes(manager.activeRevision))
		assert.equal((await manager.getPreset("search", "summary")).model, "gpt-4.1-mini")
		assert.deepEqual(stagingEntries, [])
	})
})

await run("parallel publishes of the same matching revision are idempotent", async () => {
	await withTempRoot("parallel-same-revision", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const publisher = new ConfigRegistryPublisher(root)
		const results = await Promise.allSettled([
			publisher.publish({ revision: "same_revision", activate: true }),
			publisher.publish({ revision: "same_revision", activate: true }),
		])
		const fulfilled = results.filter((result) => result.status === "fulfilled")
		const rejected = results.filter((result) => result.status === "rejected")
		const pointer = JSON.parse(await readFile(path.join(root, "current.json"), "utf-8")) as { revision: string }
		const manager = await ConfigRegistryManager.open(root)
		const stagingEntries = await readdir(path.join(root, "compiled", ".staging")).catch(() => [])

		assert.equal(fulfilled.length, 2)
		assert.equal(rejected.length, 0)
		assert.equal(pointer.revision, "same_revision")
		assert.equal(manager.activeRevision, "same_revision")
		assert.equal((await manager.getPreset("search", "summary")).model, "gpt-4.1-mini")
		assert.deepEqual(stagingEntries, [])
	})
})

await run("manager rejects a tampered resolved compiled revision on startup", async () => {
	await withTempRoot("tampered", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const resolvedPath = path.join(root, "compiled", published.revision, "resolved", "search", "summary.json")
		const resolved = JSON.parse(await readFile(resolvedPath, "utf-8")) as Record<string, unknown>
		resolved["model"] = "tampered-model"
		await writeFile(resolvedPath, JSON.stringify(resolved), "utf-8")

		await assert.rejects(ConfigRegistryManager.open(root), InvalidConfigError)
	})
})

await run("manager rejects a tampered source compiled revision on startup", async () => {
	await withTempRoot("tampered-source", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const sourcePath = path.join(root, "compiled", published.revision, "source", "search", "summary.mda")
		await writeFile(sourcePath, "---\nname: tampered\ndescription: Tampered.\nmetadata: {}\n---\n", "utf-8")

		await assert.rejects(ConfigRegistryManager.open(root), InvalidConfigError)
	})
})

await run("manager rejects a compiled revision when manifest and artifact are tampered together", async () => {
	await withTempRoot("tampered-manifest-and-artifact", async (root) => {
		await writeSourcePreset(root, "search", "summary", {
			model: "gpt-4.1-mini",
			maxOutputTokens: 256,
		})

		const published = await new ConfigRegistryPublisher(root).publish()
		const resolvedPath = path.join(root, "compiled", published.revision, "resolved", "search", "summary.json")
		const resolved = JSON.parse(await readFile(resolvedPath, "utf-8")) as Record<string, unknown>
		resolved["model"] = "tampered-model"
		const tamperedResolved = JSON.stringify(resolved, null, 2) + "\n"
		await writeFile(resolvedPath, tamperedResolved, "utf-8")

		const manifest = JSON.parse(await readFile(published.manifestPath, "utf-8")) as {
			presets: Record<string, { resolved_sha256: string }>
		}
		const manifestEntry = manifest.presets["search/summary"]
		assert.ok(manifestEntry)
		manifestEntry.resolved_sha256 = sha256Text(tamperedResolved)
		await writeFile(published.manifestPath, JSON.stringify(manifest, null, 2) + "\n", "utf-8")

		await assert.rejects(ConfigRegistryManager.open(root), InvalidConfigError)
	})
})

console.log(`\n${"=".repeat(40)}`)
console.log(`Results: ${passed} passed, ${failed} failed`)
if (failed > 0) {
	process.exit(1)
}
console.log("All tests passed!")
