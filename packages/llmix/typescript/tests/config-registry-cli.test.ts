import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { runCli } from "../src/cli.js"

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

async function withTempRoot(name: string, fn: (tempRoot: string, registryRoot: string) => Promise<void>): Promise<void> {
	const tempRoot = await mkdtemp(path.join(tmpdir(), `llmix-cli-${name}-`))
	try {
		await fn(tempRoot, path.join(tempRoot, "config", "llm"))
	} finally {
		await rm(tempRoot, { recursive: true, force: true })
	}
}

async function captureCli(args: string[]): Promise<{ exitCode: number; stdout: string; stderr: string }> {
	const stdout: string[] = []
	const stderr: string[] = []
	const exitCode = await runCli(args, {
		stdout(message) {
			stdout.push(message)
		},
		stderr(message) {
			stderr.push(message)
		},
	})
	return { exitCode, stdout: stdout.join("\n"), stderr: stderr.join("\n") }
}

function sha256Prefixed(content: string): string {
	return `sha256:${createHash("sha256").update(content).digest("hex")}`
}

function sourceSetDigest(sources: readonly Record<string, unknown>[]): string {
	return sha256Prefixed(mdaCanonicalJson({ sources }))
}

function mdaCanonicalJson(value: unknown): string {
	if (value === null) {
		return "null"
	}
	if (typeof value === "string") {
		return JSON.stringify(value)
	}
	if (typeof value === "number") {
		if (!Number.isFinite(value)) {
			throw new Error("non-finite number cannot be canonicalized")
		}
		return JSON.stringify(value)
	}
	if (typeof value === "boolean") {
		return value ? "true" : "false"
	}
	if (Array.isArray(value)) {
		return `[${value.map(mdaCanonicalJson).join(",")}]`
	}
	if (typeof value === "object") {
		const record = value as Record<string, unknown>
		return `{${Object.keys(record)
			.sort()
			.filter((key) => record[key] !== undefined)
			.map((key) => `${JSON.stringify(key)}:${mdaCanonicalJson(record[key])}`)
			.join(",")}}`
	}
	return "null"
}

await run("help exposes official registry commands", async () => {
	const result = await captureCli(["--help"])
	assert.equal(result.exitCode, 0)
	assert.match(result.stdout, /publish-registry/)
	assert.match(result.stdout, /check-registry/)
})

await run("package exposes llmix bin", async () => {
	const packagePath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "package.json")
	const packageJson = JSON.parse(await readFile(packagePath, "utf-8")) as {
		bin?: Record<string, string>
	}
	assert.equal(packageJson.bin?.["llmix"], "./dist/cli.js")
})

await run("publish-registry rejects release plan source digest mismatch", async () => {
	await withTempRoot("digest-mismatch", async (tempRoot, registryRoot) => {
		const sourceContent = "---\nname: summary\n---\n\n# Test\n"
		const sourcePath = path.join(registryRoot, "source", "search", "summary.mda")
		await mkdir(path.dirname(sourcePath), { recursive: true })
		await writeFile(sourcePath, sourceContent)
		const releasePlanPath = path.join(tempRoot, "release", "plan.json")
		await mkdir(path.dirname(releasePlanPath), { recursive: true })
		const sources = [
			{
				module: "search",
				preset: "summary",
				sourcePath: "search/summary.mda",
				rawSourceDigest: `sha256:${"0".repeat(64)}`,
				expectedRegistryEntryIdentity: "search/summary",
			},
		]
		await writeFile(
			releasePlanPath,
			`${JSON.stringify(
				{
					version: 1,
					kind: "llmix-release-plan",
					registryDir: registryRoot,
					sourceSetDigest: sourceSetDigest(sources),
					sources,
				},
				null,
				2,
			)}\n`,
		)
		const result = await captureCli([
			"publish-registry",
			"--root",
			registryRoot,
			"--release-plan",
			releasePlanPath,
			"--revision",
			"2026-05-14T000000Z",
			"--policy",
			path.join(tempRoot, "policy.json"),
			"--root-did",
			"did:web:tools.example.com",
			"--root-key-id",
			"did:web:tools.example.com#release",
			"--root-key-file",
			path.join(tempRoot, "release-key.pem"),
		])
		assert.equal(result.exitCode, 1)
		assert.match(result.stderr, /rawSourceDigest/)
	})
})

await run("publish-registry rejects release plan source set digest mismatch", async () => {
	await withTempRoot("source-set-digest-mismatch", async (tempRoot, registryRoot) => {
		const sourceContent = "---\nname: summary\n---\n\n# Test\n"
		const sourcePath = path.join(registryRoot, "source", "search", "summary.mda")
		await mkdir(path.dirname(sourcePath), { recursive: true })
		await writeFile(sourcePath, sourceContent)
		const releasePlanPath = path.join(tempRoot, "release", "plan.json")
		await mkdir(path.dirname(releasePlanPath), { recursive: true })
		const sources = [
			{
				module: "search",
				preset: "summary",
				sourcePath: "search/summary.mda",
				rawSourceDigest: sha256Prefixed(sourceContent),
				expectedRegistryEntryIdentity: "search/summary",
			},
		]
		await writeFile(
			releasePlanPath,
			`${JSON.stringify(
				{
					version: 1,
					kind: "llmix-release-plan",
					registryDir: registryRoot,
					sourceSetDigest: sha256Prefixed("tampered-source-set"),
					sources,
				},
				null,
				2,
			)}\n`,
		)
		const result = await captureCli([
			"publish-registry",
			"--root",
			registryRoot,
			"--release-plan",
			releasePlanPath,
			"--revision",
			"2026-05-14T000000Z",
			"--policy",
			path.join(tempRoot, "policy.json"),
			"--root-did",
			"did:web:tools.example.com",
			"--root-key-id",
			"did:web:tools.example.com#release",
			"--root-key-file",
			path.join(tempRoot, "release-key.pem"),
		])
		assert.equal(result.exitCode, 1)
		assert.match(result.stderr, /sourceSetDigest/)
	})
})

await run("publish-registry rejects release plan source missing from config/llm/source", async () => {
	await withTempRoot("missing-source", async (tempRoot, registryRoot) => {
		await mkdir(path.join(registryRoot, "source"), { recursive: true })
		const releasePlanPath = path.join(tempRoot, "release", "plan.json")
		await mkdir(path.dirname(releasePlanPath), { recursive: true })
		const sources = [
			{
				module: "search",
				preset: "summary",
				sourcePath: "search/summary.mda",
				rawSourceDigest: sha256Prefixed("missing"),
				expectedRegistryEntryIdentity: "search/summary",
			},
		]
		await writeFile(
			releasePlanPath,
			`${JSON.stringify(
				{
					version: 1,
					kind: "llmix-release-plan",
					registryDir: registryRoot,
					sourceSetDigest: sourceSetDigest(sources),
					sources,
				},
				null,
				2,
			)}\n`,
		)
		const result = await captureCli([
			"publish-registry",
			"--root",
			registryRoot,
			"--release-plan",
			releasePlanPath,
			"--revision",
			"2026-05-14T000000Z",
			"--policy",
			path.join(tempRoot, "policy.json"),
			"--root-did",
			"did:web:tools.example.com",
			"--root-key-id",
			"did:web:tools.example.com#release",
			"--root-key-file",
			path.join(tempRoot, "release-key.pem"),
		])
		assert.equal(result.exitCode, 1)
		assert.match(result.stderr, /missing from config\/llm\/source/)
	})
})

await run("check-registry rejects trust anchor inside config/llm", async () => {
	await withTempRoot("trust-inside-root", async (_tempRoot, registryRoot) => {
		await mkdir(registryRoot, { recursive: true })
		const trustPath = path.join(registryRoot, "trust.json")
		await writeFile(trustPath, "{}\n")
		const result = await captureCli([
			"check-registry",
			"--root",
			registryRoot,
			"--trust",
			trustPath,
			"--preset",
			"search/summary",
		])
		assert.equal(result.exitCode, 1)
		assert.match(result.stderr, /outside the registry root/)
	})
})

await run("publish-registry rejects did:web policy without verifier document", async () => {
	await withTempRoot("missing-did-document", async (tempRoot, registryRoot) => {
		const sourceContent = "---\nname: summary\n---\n\n# Test\n"
		const sourcePath = path.join(registryRoot, "source", "search", "summary.mda")
		await mkdir(path.dirname(sourcePath), { recursive: true })
		await writeFile(sourcePath, sourceContent)
		const releasePlanPath = path.join(tempRoot, "release", "plan.json")
		const policyPath = path.join(tempRoot, "release", "policy.json")
		await mkdir(path.dirname(releasePlanPath), { recursive: true })
		const sources = [
			{
				module: "search",
				preset: "summary",
				sourcePath: "search/summary.mda",
				rawSourceDigest: sha256Prefixed(sourceContent),
				expectedRegistryEntryIdentity: "search/summary",
			},
		]
		await writeFile(
			releasePlanPath,
			`${JSON.stringify(
				{
					version: 1,
					kind: "llmix-release-plan",
					registryDir: registryRoot,
					sourceSetDigest: sourceSetDigest(sources),
					sources,
				},
				null,
				2,
			)}\n`,
		)
		await writeFile(
			policyPath,
			`${JSON.stringify(
				{
					version: 1,
					trustedSigners: [{ type: "did-web", domain: "tools.example.com" }],
					minSignatures: 1,
				},
				null,
				2,
			)}\n`,
		)
		const result = await captureCli([
			"publish-registry",
			"--root",
			registryRoot,
			"--release-plan",
			releasePlanPath,
			"--revision",
			"2026-05-14T000000Z",
			"--policy",
			policyPath,
			"--root-did",
			"did:web:tools.example.com",
			"--root-key-id",
			"did:web:tools.example.com#release",
			"--root-key-file",
			path.join(tempRoot, "release-key.pem"),
		])
		assert.equal(result.exitCode, 2)
		assert.match(result.stderr, /did:web policy requires --did-document/)
	})
})

await run("publish-registry requires registry-root signing inputs", async () => {
	const result = await captureCli([
		"publish-registry",
		"--release-plan",
		"release/plan.json",
		"--revision",
		"2026-05-14T000000Z",
		"--policy",
		"release/policy.json",
		"--root-did",
		"did:web:tools.example.com",
		"--root-key-id",
		"did:web:tools.example.com#release",
	])
	assert.equal(result.exitCode, 2)
	assert.match(result.stderr, /--root-key-file is required/)
})

if (failed > 0) {
	throw new Error(`${failed} config registry CLI test(s) failed`)
}

console.log(`[PASS] config registry CLI tests completed (${passed} passed)`)
