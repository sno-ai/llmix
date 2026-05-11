import assert from "node:assert/strict"
import { Buffer } from "node:buffer"
import { execFile } from "node:child_process"
import { createHash, generateKeyPairSync, sign as cryptoSign, verify as cryptoVerify } from "node:crypto"
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import type { DidWebVerifier, TrustPolicy } from "@snoai/mda-config"

import {
	ConfigRegistryManager,
	ConfigRegistryPublisher,
	loadLlmixTrustManifest,
	registryRootOptionsFromTrustManifest,
	type RegistryRootSigner,
} from "../src/index.js"

const REQUIRED_MDA_CLI_VERSION = "1.1.2"
const DID = "did:web:tools.example.com"
const DID_DOMAIN = "tools.example.com"
const DID_WEB_SIGNER = "did-web:tools.example.com"
const DID_KEY_ID = `${DID}#release-2026`
const REGISTRY_ROOT_PAYLOAD_TYPE = "application/vnd.snoai.llmix.registry-root+json"

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
	const { stdout, stderr } = await runCommand("mda", args)
	assert.equal(stderr, "")
	return stdout
}

async function runMdaJson<T>(args: string[]): Promise<T> {
	return JSON.parse(await runMda([...args, "--json"])) as T
}

async function hasRequiredMdaCli(): Promise<boolean> {
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

function dssePae(payloadType: string, payloadBytes: Buffer): Buffer {
	return Buffer.concat([
		Buffer.from(`DSSEv1 ${Buffer.byteLength(payloadType, "utf8")} ${payloadType} ${payloadBytes.length} `, "utf8"),
		payloadBytes,
	])
}

function sha256Bytes(bytes: Buffer): string {
	return createHash("sha256").update(bytes).digest("hex")
}

if (!(await hasRequiredMdaCli())) {
	console.log(`[SKIP] mda >= ${REQUIRED_MDA_CLI_VERSION} is required for CLI bridge E2E`)
	process.exit(0)
}

const tempRoot = await mkdtemp(path.join(tmpdir(), "llmix-mda-cli-bridge-"))

try {
	const sourceDir = path.join(tempRoot, "release-source")
	const sourceModuleDir = path.join(sourceDir, "search_summary")
	const registryDir = path.join(tempRoot, "config", "llm")
	const registryAuthoringDir = path.join(registryDir, "authoring", "search_summary")
	const releaseDir = path.join(tempRoot, "release")
	await mkdir(sourceModuleDir, { recursive: true })
	await mkdir(registryAuthoringDir, { recursive: true })
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

	const unsignedPresetPath = path.join(tempRoot, "unsigned-openai-fast.mda")
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
		unsignedPresetPath,
	])
	await runMdaJson<{ ok: boolean }>(["integrity", "compute", unsignedPresetPath, "--target", "source", "--write"])

	const signedPresetPath = path.join(sourceModuleDir, "openai_fast.mda")
	await runMdaJson<{ ok: boolean }>([
		"sign",
		unsignedPresetPath,
		"--profile",
		"did-web",
		"--did",
		DID,
		"--key-id",
		DID_KEY_ID,
		"--key-file",
		keyPath,
		"--out",
		signedPresetPath,
	])
	await runMdaJson<{ ok: boolean }>([
		"verify",
		signedPresetPath,
		"--target",
		"source",
		"--policy",
		sourcePolicyPath,
		"--did-document",
		didDocumentPath,
	])
	await copyFile(signedPresetPath, path.join(registryAuthoringDir, "openai_fast.mda"))

	const releasePlanPath = path.join(releaseDir, "plan.json")
	const releasePlanResult = await runMdaJson<{ ok: boolean; sourceCount: number; sourceSetDigest: string }>([
		"release",
		"prepare",
		"--target",
		"llmix-registry",
		"--source",
		sourceDir,
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

	const didWebVerifier: DidWebVerifier = {
		async verify(input) {
			assert.equal(input.domain, DID_DOMAIN)
			assert.equal(input.keyId, DID_KEY_ID)
			assert.equal(input.algorithm, "ed25519")
			return cryptoVerify(null, Buffer.from(input.paeBytes), publicKey, Buffer.from(input.signature, "base64"))
		},
	}
	const registryRootSigner: RegistryRootSigner = ({ canonicalPayload, integrity, payloadType }) => ({
		signer: DID_WEB_SIGNER,
		"key-id": DID_KEY_ID,
		algorithm: "ed25519",
		"payload-type": payloadType,
		"payload-digest": integrity.digest,
		signature: cryptoSign(
			null,
			dssePae(REGISTRY_ROOT_PAYLOAD_TYPE, Buffer.from(canonicalPayload, "utf8")),
			privateKey,
		).toString("base64"),
	})

	const published = await new ConfigRegistryPublisher(registryDir).publish({
		revision: "2026-05-11T000000Z",
		trustedRuntime: true,
		trustPolicy: policyResult.policy,
		didWebVerifier,
		registryRoot: { signer: registryRootSigner },
	})
	assert.ok(published.registryRootPath)
	assert.ok(published.registryRootSha256)

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
		sourceDir,
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

	const manifest = await loadLlmixTrustManifest(trustManifestPath)
	const manager = await ConfigRegistryManager.open(registryDir, {
		signedRoot: registryRootOptionsFromTrustManifest(manifest, { didWebVerifier }),
	})
	const config = await manager.getPreset("search_summary", "openai_fast")
	assert.equal(config.provider, "openai")
	assert.equal(config.model, "gpt-5-mini")
	console.log(
		"CLI_BRIDGE_LOADED_CONFIG",
		JSON.stringify({
			activeRevision: manager.activeRevision,
			preset: "search_summary/openai_fast",
			provider: config.provider,
			model: config.model,
			expectedRootDigest: finalizeResult.expectedRootDigest,
		}),
	)
	console.log("[PASS] mda 1.1.2 native LLMix registry-root bridge")
} finally {
	await rm(tempRoot, { recursive: true, force: true })
}
