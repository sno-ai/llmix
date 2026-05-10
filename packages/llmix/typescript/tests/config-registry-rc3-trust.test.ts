import assert from "node:assert/strict"
import { Buffer } from "node:buffer"
import { createHash } from "node:crypto"
import { mkdtemp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import type { DidWebVerifier, RekorClient, RekorEntry, SigstoreVerifier } from "@snoai/mda-config"

import {
	ConfigRegistryManager,
	ConfigRegistryPublisher,
	loadMdaConfig,
	type ConfigRegistryPublishOptions,
	type MdaConfigLoadOptions,
	type RegistryRootEnvelope,
	type RegistryRootSigner,
} from "../src/index.js"

let passed = 0
let failed = 0

const BODY = "# Trusted registry test preset\n"
const DID_WEB_DOMAIN = "tools.example.com"
const DID_WEB_KEY_ID = "did:web:tools.example.com#release"
const SIGSTORE_ISSUER = "https://accounts.google.com"
const SIGSTORE_SUBJECT = "releases@snoai.com"
const SIGSTORE_REKOR_URL = "https://rekor.sigstore.dev"
const SIGSTORE_LOG_ID = "c0d23b6c4f200000000000000000000000000000000000000000000000000000"
const SIGSTORE_LOG_INDEX = 87654321
const SIGSTORE_KEY_ID = "fulcio:9c4e7b2f1a05c3b9e2d6c2b1e7f0a8d4c3b9e2f1a05c3b9e2d6c2b1e7f0a8d4c"
const PAYLOAD_TYPE = "application/vnd.snoai-llmix.preset+json"
const REGISTRY_ROOT_PAYLOAD_TYPE = "application/vnd.snoai.llmix.registry-root+json"
const REGISTRY_ROOT_DOMAIN = "registry.example.com"
const REGISTRY_ROOT_KEY_ID = "did:web:registry.example.com#root"

const DID_WEB_POLICY = {
	version: 1,
	trustedSigners: [{ type: "did-web", domain: DID_WEB_DOMAIN }],
} as const

const SIGSTORE_POLICY = {
	version: 1,
	trustedSigners: [{ type: "sigstore-oidc", issuer: SIGSTORE_ISSUER, subject: SIGSTORE_SUBJECT }],
	rekor: { url: SIGSTORE_REKOR_URL },
} as const

const REGISTRY_ROOT_POLICY = {
	version: 1,
	trustedSigners: [{ type: "did-web", domain: REGISTRY_ROOT_DOMAIN }],
} as const

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

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

async function withTempRoot(name: string, fn: (root: string) => Promise<void>): Promise<void> {
	const tempRoot = await mkdtemp(path.join(tmpdir(), `llmix-rc3-${name}-`))
	const root = path.join(tempRoot, "config", "llm")
	try {
		await fn(root)
	} finally {
		await rm(tempRoot, { recursive: true, force: true })
	}
}

function canonicalJson(value: JsonValue): string {
	if (Array.isArray(value)) {
		return `[${value.map((item) => canonicalJson(item)).join(",")}]`
	}
	if (value !== null && typeof value === "object") {
		return `{${Object.entries(value)
			.sort(([left], [right]) => left.localeCompare(right))
			.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
			.join(",")}}`
	}
	const encoded = JSON.stringify(value)
	if (encoded === undefined) {
		throw new Error("Unsupported JSON value")
	}
	return encoded
}

function presetFrontmatter(presetName: string): JsonValue {
	return {
		name: presetName,
		description: `${presetName} trusted registry test preset.`,
		metadata: {
			"snoai-llmix": {
				common: {
					provider: "openai",
					model: "gpt-4.1-mini",
					temperature: 0.2,
					maxOutputTokens: 256,
				},
			},
		},
	}
}

function integrityDigest(frontmatter: JsonValue): string {
	const canonicalArtifact = `---\n${canonicalJson(frontmatter)}\n---\n${BODY}`
	return `sha256:${createHash("sha256").update(canonicalArtifact).digest("hex")}`
}

function integrityPayloadBase64(digest: string): string {
	return Buffer.from(canonicalJson({ algorithm: "sha256", digest })).toString("base64")
}

function trustedDidWebMda(presetName: string): { source: string; digest: string } {
	const digest = integrityDigest(presetFrontmatter(presetName))
	return {
		digest,
		source: [
			"---",
			`name: ${presetName}`,
			`description: ${presetName} trusted registry test preset.`,
			"metadata:",
			"  snoai-llmix:",
			"    common:",
			"      provider: openai",
			"      model: gpt-4.1-mini",
			"      temperature: 0.2",
			"      maxOutputTokens: 256",
			"integrity:",
			"  algorithm: sha256",
			`  digest: "${digest}"`,
			"signatures:",
			`  - signer: "did-web:${DID_WEB_DOMAIN}"`,
			`    key-id: "${DID_WEB_KEY_ID}"`,
			`    payload-digest: "${digest}"`,
			"    algorithm: ed25519",
			'    signature: "ZmFrZS1kaWQtd2ViLXNpZ25hdHVyZQ=="',
			`    payload-type: "${PAYLOAD_TYPE}"`,
			"---",
			BODY,
		].join("\n"),
	}
}

function trustedSigstoreMda(presetName: string): { source: string; digest: string; rekorEntry: RekorEntry } {
	const digest = integrityDigest(presetFrontmatter(presetName))
	const rekorEntry: RekorEntry = {
		kind: "dsse-v0.0.1",
		logId: SIGSTORE_LOG_ID,
		logIndex: SIGSTORE_LOG_INDEX,
		inclusionVerified: true,
		certificatePem: "",
		dsseEnvelope: {
			payloadType: PAYLOAD_TYPE,
			payload: integrityPayloadBase64(digest),
			signatures: [{ sig: "MEUCIQDkXFIXTUREONLYBASE64==", keyid: SIGSTORE_KEY_ID }],
		},
	}
	return {
		digest,
		rekorEntry,
		source: [
			"---",
			`name: ${presetName}`,
			`description: ${presetName} trusted registry test preset.`,
			"metadata:",
			"  snoai-llmix:",
			"    common:",
			"      provider: openai",
			"      model: gpt-4.1-mini",
			"      temperature: 0.2",
			"      maxOutputTokens: 256",
			"integrity:",
			"  algorithm: sha256",
			`  digest: "${digest}"`,
			"signatures:",
			`  - signer: "sigstore-oidc:${SIGSTORE_ISSUER}"`,
			`    key-id: "${SIGSTORE_KEY_ID}"`,
			`    payload-digest: "${digest}"`,
			"    algorithm: ecdsa-p256",
			'    signature: "MEUCIQDkXFIXTUREONLYBASE64=="',
			`    rekor-log-id: "${SIGSTORE_LOG_ID}"`,
			`    rekor-log-index: ${SIGSTORE_LOG_INDEX}`,
			`    payload-type: "${PAYLOAD_TYPE}"`,
			"---",
			BODY,
		].join("\n"),
	}
}

async function writeAuthoringSource(root: string, moduleName: string, presetName: string, source: string): Promise<string> {
	const filePath = path.join(root, "authoring", moduleName, `${presetName}.mda`)
	await mkdir(path.dirname(filePath), { recursive: true })
	await writeFile(filePath, source, "utf-8")
	return filePath
}

async function writeUnsignedAuthoringPreset(root: string): Promise<void> {
	await writeAuthoringSource(
		root,
		"search",
		"summary",
		[
			"---",
			"name: summary",
			"description: Baseline registry test preset.",
			"metadata:",
			"  snoai-llmix:",
			"    common:",
			"      provider: openai",
			"      model: gpt-4.1-mini",
			"      maxOutputTokens: 128",
			"---",
			BODY,
		].join("\n"),
	)
}

function didWebVerifier(trusted: boolean, expectedDigest: string): DidWebVerifier {
	return {
		async verify(input) {
			const payload = JSON.parse(new TextDecoder().decode(input.payloadBytes)) as { digest?: unknown }
			return (
				trusted &&
				input.domain === DID_WEB_DOMAIN &&
				input.keyId === DID_WEB_KEY_ID &&
				input.payloadType === PAYLOAD_TYPE &&
				payload.digest === expectedDigest
			)
		},
	}
}

function rekorClient(entry: RekorEntry | null): RekorClient {
	return {
		url: SIGSTORE_REKOR_URL,
		async fetchEntry(rekorUrl, logId, logIndex) {
			if (rekorUrl !== SIGSTORE_REKOR_URL || logId !== SIGSTORE_LOG_ID || logIndex !== SIGSTORE_LOG_INDEX) {
				return null
			}
			return entry
		},
	}
}

function sigstoreVerifier(trusted: boolean): SigstoreVerifier {
	return {
		async verify(entry, signature) {
			if (!trusted || entry.logId !== SIGSTORE_LOG_ID || signature["key-id"] !== SIGSTORE_KEY_ID) {
				throw new Error("fixture Sigstore verifier rejected the signature")
			}
			return {
				identity: {
					issuer: SIGSTORE_ISSUER,
					subjectAlternativeName: SIGSTORE_SUBJECT,
				},
			}
		},
	}
}

async function currentRevision(root: string): Promise<string> {
	const pointer = JSON.parse(await readFile(path.join(root, "current.json"), "utf-8")) as { revision?: unknown }
	assert.equal(typeof pointer.revision, "string")
	return pointer.revision as string
}

async function sha256File(filePath: string): Promise<string> {
	return createHash("sha256").update(await readFile(filePath)).digest("hex")
}

async function assertNoStagingEntries(root: string): Promise<void> {
	const entries = await readdir(path.join(root, "snapshots", ".staging")).catch(() => [])
	assert.deepEqual(entries, [])
}

function registryRootSignature(payloadSha256: string): string {
	return Buffer.from(`registry-root:${payloadSha256}`).toString("base64url")
}

function registryRootSigner(): RegistryRootSigner {
	return ({ integrity, payloadSha256, payloadType }) => ({
		algorithm: "ed25519",
		signer: `did-web:${REGISTRY_ROOT_DOMAIN}`,
		"key-id": REGISTRY_ROOT_KEY_ID,
		"payload-digest": integrity.digest,
		"payload-type": payloadType,
		signature: registryRootSignature(payloadSha256),
	})
}

function registryRootVerifier(trusted = true): DidWebVerifier {
	return {
		async verify(input) {
			const payload = JSON.parse(new TextDecoder().decode(input.payloadBytes)) as { digest?: unknown }
			const digest = typeof payload.digest === "string" ? payload.digest : ""
			const payloadSha256 = digest.startsWith("sha256:") ? digest.slice("sha256:".length) : ""
			return (
				trusted &&
				input.domain === REGISTRY_ROOT_DOMAIN &&
				input.keyId === REGISTRY_ROOT_KEY_ID &&
				input.algorithm === "ed25519" &&
				input.payloadType === REGISTRY_ROOT_PAYLOAD_TYPE &&
				input.signature === registryRootSignature(payloadSha256)
			)
		},
	}
}

async function readRegistryRoot(root: string, revision: string): Promise<RegistryRootEnvelope> {
	return JSON.parse(await readFile(path.join(root, "snapshots", revision, "registry-root.json"), "utf-8")) as RegistryRootEnvelope
}

await run("direct MDA loading accepts RC3 trustedRuntime did:web options", async () => {
	await withTempRoot("direct-did-web", async (root) => {
		const { source, digest } = trustedDidWebMda("direct-did-web")
		const filePath = await writeAuthoringSource(root, "search", "direct_did_web", source)
		const options: MdaConfigLoadOptions = {
			trustedRuntime: true,
			trustPolicy: DID_WEB_POLICY,
			didWebVerifier: didWebVerifier(true, digest),
		}

		const loaded = await loadMdaConfig(filePath, options)

		assert.equal(loaded.provider, "openai")
		assert.equal(loaded.model, "gpt-4.1-mini")
		assert.equal(loaded.common?.maxOutputTokens, 256)
	})
})

await run("publisher keeps active revision when did:web trustedRuntime verification rejects", async () => {
	await withTempRoot("publisher-did-web-reject", async (root) => {
		await writeUnsignedAuthoringPreset(root)
		const first = await new ConfigRegistryPublisher(root).publish()
		const { source, digest } = trustedDidWebMda("summary")
		await writeAuthoringSource(root, "search", "summary", source)

		const options: ConfigRegistryPublishOptions = {
			trustedRuntime: true,
			trustPolicy: DID_WEB_POLICY,
			didWebVerifier: didWebVerifier(false, digest),
		}
		await assert.rejects(new ConfigRegistryPublisher(root).publish(options), Error)

		assert.equal(await currentRevision(root), first.revision)
		await assertNoStagingEntries(root)
	})
})

await run("publisher accepts did:web trustedRuntime verification and publishes resolved snapshot", async () => {
	await withTempRoot("publisher-did-web-accept", async (root) => {
		const { source, digest } = trustedDidWebMda("summary")
		await writeAuthoringSource(root, "search", "summary", source)

		const published = await new ConfigRegistryPublisher(root).publish({
			trustedRuntime: true,
			trustPolicy: DID_WEB_POLICY,
			didWebVerifier: didWebVerifier(true, digest),
		})
		const manager = await ConfigRegistryManager.open(root)
		const resolved = JSON.parse(
			await readFile(path.join(root, "snapshots", published.revision, "resolved", "search", "summary.json"), "utf-8"),
		) as { model?: unknown }

		assert.equal(await currentRevision(root), published.revision)
		assert.equal(manager.activeRevision, published.revision)
		assert.equal((await manager.getPreset("search", "summary")).model, "gpt-4.1-mini")
		assert.equal(resolved.model, "gpt-4.1-mini")
	})
})

await run("publisher keeps active revision when Sigstore trustedRuntime Rekor lookup rejects", async () => {
	await withTempRoot("publisher-sigstore-reject", async (root) => {
		await writeUnsignedAuthoringPreset(root)
		const first = await new ConfigRegistryPublisher(root).publish()
		const { source } = trustedSigstoreMda("summary")
		await writeAuthoringSource(root, "search", "summary", source)

		const options: ConfigRegistryPublishOptions = {
			trustedRuntime: true,
			trustPolicy: SIGSTORE_POLICY,
			rekorClient: rekorClient(null),
			sigstoreVerifier: sigstoreVerifier(true),
		}
		await assert.rejects(new ConfigRegistryPublisher(root).publish(options), Error)

		assert.equal(await currentRevision(root), first.revision)
		await assertNoStagingEntries(root)
	})
})

await run("publisher accepts Sigstore trustedRuntime verification and publishes resolved snapshot", async () => {
	await withTempRoot("publisher-sigstore-accept", async (root) => {
		const { source, rekorEntry } = trustedSigstoreMda("summary")
		await writeAuthoringSource(root, "search", "summary", source)

		const published = await new ConfigRegistryPublisher(root).publish({
			trustedRuntime: true,
			trustPolicy: SIGSTORE_POLICY,
			rekorClient: rekorClient(rekorEntry),
			sigstoreVerifier: sigstoreVerifier(true),
		})
		const manager = await ConfigRegistryManager.open(root)
		const authoring = await readFile(
			path.join(root, "snapshots", published.revision, "authoring", "search", "summary.mda"),
			"utf-8",
		)

		assert.equal(await currentRevision(root), published.revision)
		assert.equal(manager.activeRevision, published.revision)
		assert.equal((await manager.getPreset("search", "summary")).common?.maxOutputTokens, 256)
		assert.match(authoring, /signer: "sigstore-oidc:https:\/\/accounts\.google\.com"/)
	})
})

await run("publisher writes signed registry root covering current binding, manifest, authoring MDA, and resolved JSON", async () => {
	await withTempRoot("signed-root-payload", async (root) => {
		await writeUnsignedAuthoringPreset(root)

		const published = await new ConfigRegistryPublisher(root).publish({
			revision: "2026-05-10T00:00:00.000Z",
			registryRoot: { signer: registryRootSigner() },
		})
		const envelope = await readRegistryRoot(root, published.revision)
		const payload = envelope.payload
		const authoringDigest = payload.files.find(
			(file) => file.role === "authoring" && file.path.endsWith("/authoring/search/summary.mda"),
		)
		const resolvedDigest = payload.files.find(
			(file) => file.role === "resolved" && file.path.endsWith("/resolved/search/summary.json"),
		)
		assert.ok(authoringDigest, "registry root should cover copied authoring .mda")
		assert.ok(resolvedDigest, "registry root should cover resolved JSON")

		const resolved = JSON.parse(await readFile(path.join(root, resolvedDigest.path), "utf-8")) as { model?: unknown }

		assert.equal(published.registryRootPath, path.join(root, "snapshots", published.revision, "registry-root.json"))
		assert.equal(published.registryRootSha256, envelope.payload_sha256)
		assert.equal(envelope.integrity.digest, `sha256:${envelope.payload_sha256}`)
		assert.equal(payload.revision, published.revision)
		assert.equal(payload.current.path, "current.json")
		assert.equal(payload.current.revision, published.revision)
		assert.equal(payload.current.manifest_sha256, published.manifestSha256)
		assert.equal(payload.current.sha256, await sha256File(path.join(root, "current.json")))
		assert.equal(payload.manifest.path, `snapshots/${published.revision}/manifest.json`)
		assert.equal(payload.manifest.sha256, published.manifestSha256)
		assert.equal(await sha256File(path.join(root, authoringDigest.path)), authoringDigest.sha256)
		assert.equal(await sha256File(path.join(root, resolvedDigest.path)), resolvedDigest.sha256)
		assert.equal(resolved.model, "gpt-4.1-mini")
	})
})

await run("runtime opens signed registry root with external verifier and rejects untrusted roots", async () => {
	await withTempRoot("signed-root-runtime", async (root) => {
		await writeUnsignedAuthoringPreset(root)

		const published = await new ConfigRegistryPublisher(root).publish({
			registryRoot: { signer: registryRootSigner() },
		})
		const rootDigest = published.registryRootSha256
		if (rootDigest === undefined) {
			throw new Error("publish should return a registry root digest")
		}
		const manager = await ConfigRegistryManager.open(root, {
			signedRoot: {
				trustPolicy: REGISTRY_ROOT_POLICY,
				didWebVerifier: registryRootVerifier(true),
				expectedRootDigest: rootDigest,
			},
		})

		assert.equal(manager.activeRevision, published.revision)
		assert.equal((await manager.getPreset("search", "summary")).model, "gpt-4.1-mini")
		await assert.rejects(
			ConfigRegistryManager.open(root, {
				signedRoot: {
					trustPolicy: REGISTRY_ROOT_POLICY,
					didWebVerifier: registryRootVerifier(false),
				},
			}),
			/no cryptographically verified signature/,
		)
	})
})

await run("runtime rejects signed registry root rollback and freshness policy violations", async () => {
	await withTempRoot("signed-root-freshness", async (root) => {
		await writeUnsignedAuthoringPreset(root)

		const older = await new ConfigRegistryPublisher(root).publish({
			revision: "2026-05-10T00:00:00.000Z",
			registryRoot: { signer: registryRootSigner() },
		})
		const newer = await new ConfigRegistryPublisher(root).publish({
			revision: "2026-05-10T00:00:01.000Z",
			registryRoot: { signer: registryRootSigner() },
		})

		await writeFile(
			path.join(root, "current.json"),
			`${JSON.stringify({ revision: older.revision, manifest_sha256: older.manifestSha256 }, null, 2)}\n`,
			"utf-8",
		)
		await assert.rejects(
			ConfigRegistryManager.open(root, {
				signedRoot: {
					trustPolicy: REGISTRY_ROOT_POLICY,
					didWebVerifier: registryRootVerifier(true),
					minimumRevision: newer.revision,
				},
			}),
			/older than minimum/,
		)
		await assert.rejects(
			ConfigRegistryManager.open(root, {
				signedRoot: {
					trustPolicy: REGISTRY_ROOT_POLICY,
					didWebVerifier: registryRootVerifier(true),
					minimumPublishedAt: new Date(Date.now() + 60_000),
				},
			}),
			/older than minimum/,
		)
		await assert.rejects(
			ConfigRegistryManager.open(root, {
				signedRoot: {
					trustPolicy: REGISTRY_ROOT_POLICY,
					didWebVerifier: registryRootVerifier(true),
					expectedRootDigest: "0".repeat(64),
				},
			}),
			/expectedRootDigest/,
		)
		await assert.rejects(
			ConfigRegistryManager.open(root, {
				signedRoot: {
					trustPolicy: REGISTRY_ROOT_POLICY,
					didWebVerifier: registryRootVerifier(true),
					highWatermark: () => false,
				},
			}),
			/high-watermark/,
		)
	})
})

await run("runtime rejects numeric custom revision rollback with minimumRevision", async () => {
	await withTempRoot("signed-root-numeric-revision", async (root) => {
		await writeUnsignedAuthoringPreset(root)

		const older = await new ConfigRegistryPublisher(root).publish({
			revision: "9",
			registryRoot: { signer: registryRootSigner() },
		})
		const newer = await new ConfigRegistryPublisher(root).publish({
			revision: "10",
			registryRoot: { signer: registryRootSigner() },
		})

		await writeFile(
			path.join(root, "current.json"),
			`${JSON.stringify({ revision: older.revision, manifest_sha256: older.manifestSha256 }, null, 2)}\n`,
			"utf-8",
		)

		await assert.rejects(
			ConfigRegistryManager.open(root, {
				signedRoot: {
					trustPolicy: REGISTRY_ROOT_POLICY,
					didWebVerifier: registryRootVerifier(true),
					minimumRevision: newer.revision,
				},
			}),
			/older than minimum/,
		)
	})
})

console.log(`\n${"=".repeat(40)}`)
console.log(`Results: ${passed} passed, ${failed} failed`)
if (failed > 0) {
	process.exit(1)
}
console.log("All tests passed!")
