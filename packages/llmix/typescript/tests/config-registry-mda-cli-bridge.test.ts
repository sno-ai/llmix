import assert from "node:assert/strict"
import { execFile } from "node:child_process"
import { createHash, generateKeyPairSync } from "node:crypto"
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import type { TrustPolicy } from "@snoai/mda-config"

import { runCli } from "../src/cli.js"
import { buildVerificationHooks } from "../src/config-registry-cli-support.js"
import {
	ConfigRegistryManager,
	loadLlmixTrustManifest,
	registryRootOptionsFromTrustManifest,
} from "../src/index.js"

const REQUIRED_MDA_CLI_VERSION = "1.1.2"
const DID = "did:web:tools.example.com"
const DID_DOMAIN = "tools.example.com"
const DID_KEY_ID = `${DID}#release-2026`
const MDA_CLI = process.env["MDA_CLI"]

async function runCommand(command: string, args: string[]): Promise<{ stdout: string; stderr: string }> {
	return await new Promise((resolve, reject) => {
		execFile(command, args, { encoding: "utf8" }, (error, stdout, stderr) => {
			if (error !== null) {
				reject(new Error(`${command} ${args.join(" ")}\nstdout=${stdout}\nstderr=${stderr}`))
				return
			}
			resolve({ stdout, stderr })
		})
	})
}

async function runMda(args: string[]): Promise<string> {
	assert.ok(MDA_CLI)
	const { stdout, stderr } = await runCommand(MDA_CLI, args)
	assert.equal(stderr, "")
	return stdout
}

async function runMdaJson<T>(args: string[]): Promise<T> {
	return JSON.parse(await runMda([...args, "--json"])) as T
}

async function runLlmixJson<T>(args: string[]): Promise<T> {
	const stdout: string[] = []
	const stderr: string[] = []
	const exitCode = await runCli([...args, "--json"], {
		stdout(message) {
			stdout.push(message)
		},
		stderr(message) {
			stderr.push(message)
		},
	})
	assert.equal(exitCode, 0, stderr.join("\n"))
	return JSON.parse(stdout.join("\n")) as T
}

async function hasRequiredMdaCli(): Promise<boolean> {
	if (MDA_CLI === undefined || MDA_CLI.length === 0) {
		return false
	}
	try {
		const version = (await runMda(["--version"])).trim()
		return compareSemver(version, REQUIRED_MDA_CLI_VERSION) >= 0
	} catch {
		return false
	}
}

function compareSemver(left: string, right: string): number {
	const leftParts = left.split(".").map((part) => Number.parseInt(part, 10))
	const rightParts = right.split(".").map((part) => Number.parseInt(part, 10))
	for (let index = 0; index < 3; index++) {
		const leftPart = semverPart(leftParts, index)
		const rightPart = semverPart(rightParts, index)
		if (leftPart !== rightPart) {
			return leftPart < rightPart ? -1 : 1
		}
	}
	return 0
}

function semverPart(parts: readonly number[], index: number): number {
	const value = parts[index]
	return value === undefined || !Number.isFinite(value) ? 0 : value
}

function sha256Bytes(bytes: Buffer): string {
	return createHash("sha256").update(bytes).digest("hex")
}

if (!(await hasRequiredMdaCli())) {
	console.log(`[SKIP] MDA_CLI pointing to mda >= ${REQUIRED_MDA_CLI_VERSION} is required for CLI bridge E2E`)
	process.exit(0)
}

const tempRoot = await mkdtemp(path.join(tmpdir(), "llmix-mda-cli-bridge-"))

try {
	const registryDir = path.join(tempRoot, "config", "llm")
	const registrySourceDir = path.join(registryDir, "source")
	const registrySourceModuleDir = path.join(registrySourceDir, "search_summary")
	const releaseDir = path.join(tempRoot, "release")
	await mkdir(registrySourceModuleDir, { recursive: true })
	await mkdir(releaseDir, { recursive: true })

	const { privateKey, publicKey } = generateKeyPairSync("ed25519")
	const keyPath = path.join(tempRoot, "did-web-release-key.pem")
	const didDocumentPath = path.join(tempRoot, "did-web-document.json")
	await writeFile(keyPath, privateKey.export({ format: "pem", type: "pkcs8" }))
	await writeFile(
		didDocumentPath,
		`${JSON.stringify({
			id: DID,
			verificationMethod: [
				{ id: DID_KEY_ID, type: "JsonWebKey2020", controller: DID, publicKeyJwk: publicKey.export({ format: "jwk" }) },
			],
		})}\n`,
	)

	const sourcePolicyPath = path.join(releaseDir, "source-policy.json")
	const policyResult = await runMdaJson<{ ok: boolean; policy: TrustPolicy }>([
		"release",
		"trust",
		"policy",
		"--target",
		"llmix-registry",
		"--profile",
		"did-web",
		"--domain",
		DID_DOMAIN,
		"--out",
		sourcePolicyPath,
	])
	assert.equal(policyResult.ok, true)

	const presetPath = path.join(registrySourceModuleDir, "openai_fast.mda")
	await runMdaJson<{ ok: boolean }>([
		"init",
		"--template",
		"llmix-preset",
		"--module",
		"search_summary",
		"--preset",
		"openai_fast",
		"--provider",
		"openai",
		"--model",
		"gpt-5-mini",
		"--out",
		presetPath,
	])
	await runMdaJson<{ ok: boolean }>(["validate", presetPath, "--target", "source"])
	await runMdaJson<{ ok: boolean }>(["integrity", "compute", presetPath, "--target", "source", "--write"])

	await runMdaJson<{ ok: boolean }>([
		"sign",
		presetPath,
		"--profile",
		"did-web",
		"--did",
		DID,
		"--key-id",
		DID_KEY_ID,
		"--key-file",
		keyPath,
		"--in-place",
	])
	await runMdaJson<{ ok: boolean }>([
		"verify",
		presetPath,
		"--target",
		"source",
		"--policy",
		sourcePolicyPath,
		"--did-document",
		didDocumentPath,
	])

	const releasePlanPath = path.join(releaseDir, "plan.json")
	const releasePlanResult = await runMdaJson<{ ok: boolean; sourceCount: number; sourceSetDigest: string }>([
		"release",
		"prepare",
		"--target",
		"llmix-registry",
		"--source",
		registrySourceDir,
		"--registry-dir",
		registryDir,
		"--policy",
		sourcePolicyPath,
		"--did-document",
		didDocumentPath,
		"--out",
		releasePlanPath,
	])
	assert.equal(releasePlanResult.ok, true)
	assert.equal(releasePlanResult.sourceCount, 1)
	assert.match(releasePlanResult.sourceSetDigest, /^sha256:[a-f0-9]{64}$/)

	const published = await runLlmixJson<{
		ok: boolean
		revision: string
		registryRootPath: string
		registryRootSha256: string
		presetIds: string[]
	}>([
		"publish-registry",
		"--root",
		registryDir,
		"--release-plan",
		releasePlanPath,
		"--revision",
		"2026-05-11T000000Z",
		"--policy",
		sourcePolicyPath,
		"--did-document",
		didDocumentPath,
		"--root-did",
		DID,
		"--root-key-id",
		DID_KEY_ID,
		"--root-key-file",
		keyPath,
	])
	assert.equal(published.ok, true)
	assert.ok(published.registryRootPath)
	assert.ok(published.registryRootSha256)
	assert.deepEqual(published.presetIds, ["search_summary/openai_fast"])
	const currentPath = path.join(registryDir, "current.json")
	const current = JSON.parse(await readFile(currentPath, "utf8")) as { revision: string }
	assert.equal(current.revision, "2026-05-11T000000Z")
	const compiledRevisionDir = path.join(registryDir, "compiled", "2026-05-11T000000Z")
	assert.equal(published.registryRootPath, path.join(compiledRevisionDir, "registry-root.json"))
	assert.ok(JSON.parse(await readFile(path.join(compiledRevisionDir, "manifest.json"), "utf8")))
	assert.ok(await readFile(path.join(compiledRevisionDir, "source", "search_summary", "openai_fast.mda")))
	const resolved = JSON.parse(
		await readFile(path.join(compiledRevisionDir, "resolved", "search_summary", "openai_fast.json"), "utf8"),
	) as { provider: string; model: string }
	assert.equal(resolved.provider, "openai")
	assert.equal(resolved.model, "gpt-5-mini")

	const trustManifestPath = path.join(releaseDir, "llmix-trust.json")
	const finalizeResult = await runMdaJson<{ ok: boolean; expectedRootDigest: string; sourceSetDigest: string }>([
		"release",
		"finalize",
		"--target",
		"llmix-registry",
		"--registry-dir",
		registryDir,
		"--registry-root",
		published.registryRootPath,
		"--release-plan",
		releasePlanPath,
		"--policy",
		sourcePolicyPath,
		"--did-document",
		didDocumentPath,
		"--derive-root-digest",
		"--minimum-revision",
		"2026-05-11T000000Z",
		"--out",
		trustManifestPath,
	])
	const registryRootBytes = await readFile(published.registryRootPath)
	assert.equal(finalizeResult.ok, true)
	assert.equal(finalizeResult.expectedRootDigest, `sha256:${sha256Bytes(registryRootBytes)}`)
	assert.equal(finalizeResult.expectedRootDigest, `sha256:${published.registryRootSha256}`)
	assert.equal(finalizeResult.sourceSetDigest, releasePlanResult.sourceSetDigest)

	const doctorResult = await runMdaJson<{ ok: boolean }>([
		"doctor",
		"release",
		"--target",
		"llmix-registry",
		"--source",
		registrySourceDir,
		"--registry-dir",
		registryDir,
		"--release-plan",
		releasePlanPath,
		"--manifest",
		trustManifestPath,
		"--did-document",
		didDocumentPath,
	])
	assert.equal(doctorResult.ok, true)

	const checked = await runLlmixJson<{
		ok: boolean
		activeRevision: string
		preset: string
		provider: string
		model: string
		tamperProof: { rejected: boolean; message: string }
	}>([
		"check-registry",
		"--root",
		registryDir,
		"--trust",
		trustManifestPath,
		"--preset",
		"search_summary/openai_fast",
		"--did-document",
		didDocumentPath,
		"--tamper-proof",
	])
	assert.equal(checked.ok, true)
	assert.equal(checked.provider, "openai")
	assert.equal(checked.model, "gpt-5-mini")
	assert.equal(checked.tamperProof.rejected, true)

	const trust = await loadLlmixTrustManifest(trustManifestPath)
	assert.equal(trust.minimumRevision, "2026-05-11T000000Z")
	const hooks = await buildVerificationHooks({
		didDocumentPaths: [didDocumentPath],
		rekorEntryPaths: [],
		rekorUrl: null,
		policy: trust.registryRootTrustPolicy,
	})
	const signedRoot = registryRootOptionsFromTrustManifest(trust, hooks)
	assert.equal(signedRoot.minimumRevision, "2026-05-11T000000Z")
	const registry = await ConfigRegistryManager.open(registryDir, { signedRoot })
	const runtimePreset = await registry.getPreset("search_summary", "openai_fast")
	assert.equal(registry.activeRevision, "2026-05-11T000000Z")
	assert.equal(runtimePreset.provider, "openai")
	assert.equal(runtimePreset.model, "gpt-5-mini")

	const tamperedRegistryDir = path.join(tempRoot, "tampered-config-llm")
	await cp(registryDir, tamperedRegistryDir, { recursive: true })
	const tamperedCurrentPath = path.join(tamperedRegistryDir, "current.json")
	const tamperedCurrent = JSON.parse(await readFile(tamperedCurrentPath, "utf8")) as {
		revision: string
		manifest_sha256: string
	}
	assert.equal(tamperedCurrent.revision, "2026-05-11T000000Z")
	tamperedCurrent.manifest_sha256 = "0".repeat(64)
	await writeFile(tamperedCurrentPath, `${JSON.stringify(tamperedCurrent, null, 2)}\n`, "utf8")
	await assert.rejects(
		ConfigRegistryManager.open(tamperedRegistryDir, { signedRoot }),
		/registry root|current binding|checksum|digest|integrity|signature/i,
	)

	console.log(
		"CLI_BRIDGE_LOADED_CONFIG",
		JSON.stringify({
			activeRevision: checked.activeRevision,
			preset: checked.preset,
			provider: checked.provider,
			model: checked.model,
			expectedRootDigest: finalizeResult.expectedRootDigest,
			tamperRejected: checked.tamperProof.rejected,
			runtimeApiOpened: true,
			runtimeApiTamperRejected: true,
			minimumRevisionPinned: true,
		}),
	)
	console.log("[PASS] mda 1.1.2 native LLMix registry-root bridge")
} finally {
	await rm(tempRoot, { recursive: true, force: true })
}
