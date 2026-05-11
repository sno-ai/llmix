import { verifySignatures, type DidWebVerifier, type RekorClient, type SignatureEntry, type SigstoreVerifier } from "@snoai/mda-config"

import { InvalidConfigError, SecurityError } from "./types.js"
import {
	canonicalCompactJsonString,
	canonicalJsonString,
	compareRevision,
	currentPointerSha256,
	ensureNumberField,
	ensureObjectField,
	ensureStringField,
	isJsonObject,
	normalizeSha256Digest,
	sha256Bytes,
	snapshotRegistryPath,
	snapshotRelativePath,
	validateRevision,
	validateSha256,
} from "./config-registry-common.js"
import {
	REGISTRY_ROOT_ENVELOPE_SCHEMA,
	REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
	REGISTRY_ROOT_PAYLOAD_TYPE,
	REGISTRY_ROOT_SCHEMA,
	REGISTRY_ROOT_SCHEMA_VERSION,
	type CurrentPointer,
	type JsonObject,
	type RegistryManifest,
	type RegistryRootCurrentBinding,
	type RegistryRootEnvelope,
	type RegistryRootFileDigest,
	type RegistryRootHighWatermark,
	type RegistryRootIntegrity,
	type RegistryRootManifestBinding,
	type RegistryRootPayload,
	type RegistryRootSignature,
	type RegistryRootSigningInput,
	type RegistryRootSigningOptions,
	type RegistryRootVerificationOptions,
} from "./config-registry-types.js"

export function registryRootFileDigests(manifest: RegistryManifest): RegistryRootFileDigest[] {
	const files: RegistryRootFileDigest[] = []
	for (const [presetId, entry] of Object.entries(manifest.presets).sort(([left], [right]) => left.localeCompare(right))) {
		const presetFiles: RegistryRootFileDigest[] = [
			{
				path: snapshotRegistryPath(manifest.revision, entry.authoring_path),
				sha256: entry.authoring_sha256,
				role: "authoring",
			},
			{
				path: snapshotRegistryPath(manifest.revision, entry.resolved_path),
				sha256: entry.resolved_sha256,
				role: "resolved",
			},
		]
		for (const file of presetFiles) {
			validateSha256(file.sha256, `registry root file ${file.path} (${presetId})`)
		}
		files.push(...presetFiles)
	}
	return sortedRegistryRootFiles(files)
}

export function sortedRegistryRootFiles(files: readonly RegistryRootFileDigest[]): RegistryRootFileDigest[] {
	const sorted = [...files].sort((left, right) => left.path.localeCompare(right.path))
	const seen = new Set<string>()
	for (const file of sorted) {
		if (seen.has(file.path)) {
			throw new InvalidConfigError(`Registry root contains duplicate file path: ${file.path}`)
		}
		seen.add(file.path)
	}
	return sorted
}

export function buildRegistryRootPayload(manifest: RegistryManifest, manifestSha256: string): RegistryRootPayload {
	validateSha256(manifestSha256, "registry root manifest")
	const currentPointer: CurrentPointer = { revision: manifest.revision, manifestSha256 }
	return {
		schema: REGISTRY_ROOT_SCHEMA,
		schema_version: REGISTRY_ROOT_SCHEMA_VERSION,
		revision: manifest.revision,
		published_at: manifest.published_at,
		current: {
			path: "current.json",
			revision: manifest.revision,
			manifest_sha256: manifestSha256,
			sha256: currentPointerSha256(currentPointer),
		},
		manifest: {
			path: snapshotRegistryPath(manifest.revision, "manifest.json"),
			sha256: manifestSha256,
		},
		files: registryRootFileDigests(manifest),
	}
}

function canonicalRegistryRootPayload(payload: RegistryRootPayload): string {
	return canonicalCompactJsonString(payload)
}

function registryRootIntegrity(payloadSha256: string): RegistryRootIntegrity {
	validateSha256(payloadSha256, "registry root payload")
	return {
		algorithm: "sha256",
		digest: `sha256:${payloadSha256}`,
	}
}

function registryRootSigningInput(payload: RegistryRootPayload): RegistryRootSigningInput {
	const canonicalPayload = canonicalRegistryRootPayload(payload)
	const payloadSha256 = sha256Bytes(canonicalPayload)
	return {
		payload,
		canonicalPayload,
		integrity: registryRootIntegrity(payloadSha256),
		payloadType: REGISTRY_ROOT_PAYLOAD_TYPE,
		payloadSha256,
	}
}

function registryRootSigningInputForEnvelope(envelope: RegistryRootEnvelope): RegistryRootSigningInput {
	const signingInput = registryRootSigningInput(envelope.payload)
	if (signingInput.payloadSha256 === envelope.payload_sha256 && signingInput.integrity.digest === envelope.integrity.digest) {
		return signingInput
	}

	const canonicalPayload = canonicalJsonString(envelope.payload)
	const payloadSha256 = sha256Bytes(canonicalPayload)
	const integrity = registryRootIntegrity(payloadSha256)
	if (payloadSha256 !== envelope.payload_sha256 || integrity.digest !== envelope.integrity.digest) {
		throw new InvalidConfigError("Registry root payload digest mismatch")
	}
	return {
		payload: envelope.payload,
		canonicalPayload,
		integrity,
		payloadType: REGISTRY_ROOT_PAYLOAD_TYPE,
		payloadSha256,
	}
}

function normalizeRegistryRootSignature(signature: SignatureEntry): RegistryRootSignature {
	const serialized = JSON.stringify(signature)
	if (serialized === undefined) {
		throw new InvalidConfigError("Registry root signature must be JSON serializable")
	}
	const normalized: unknown = JSON.parse(serialized)
	return parseRegistryRootSignature(normalized, "registry root signer")
}

function requiredSignatureCount(minSignatures: number | undefined): number {
	if (minSignatures === undefined) {
		return 1
	}
	if (!Number.isInteger(minSignatures) || minSignatures < 1) {
		throw new InvalidConfigError("Registry root minSignatures must be an integer >= 1")
	}
	return minSignatures
}

function parseRegistryRootFileDigest(value: unknown, sourcePath: string): RegistryRootFileDigest {
	if (!isJsonObject(value)) {
		throw new InvalidConfigError(`Registry root file entry must be an object: ${sourcePath}`)
	}
	const role = ensureStringField(value, "role", sourcePath)
	if (role !== "authoring" && role !== "resolved") {
		throw new InvalidConfigError(`Registry root file entry has invalid role: ${sourcePath}`)
	}
	const sha256 = ensureStringField(value, "sha256", sourcePath)
	validateSha256(sha256, `registry root file ${ensureStringField(value, "path", sourcePath)}`)
	return {
		path: ensureStringField(value, "path", sourcePath),
		sha256,
		role,
	}
}

function parseRegistryRootPayload(value: JsonObject, sourcePath: string): RegistryRootPayload {
	if (ensureStringField(value, "schema", sourcePath) !== REGISTRY_ROOT_SCHEMA) {
		throw new InvalidConfigError(`Unsupported registry root payload schema in ${sourcePath}`)
	}
	if (ensureNumberField(value, "schema_version", sourcePath) !== REGISTRY_ROOT_SCHEMA_VERSION) {
		throw new InvalidConfigError(`Unsupported registry root payload schema version in ${sourcePath}`)
	}
	const revision = ensureStringField(value, "revision", sourcePath)
	validateRevision(revision)
	const current = parseRegistryRootCurrent(ensureObjectField(value, "current", sourcePath), sourcePath)
	const manifest = parseRegistryRootManifest(ensureObjectField(value, "manifest", sourcePath), sourcePath)
	const filesValue = value["files"]
	if (!Array.isArray(filesValue)) {
		throw new InvalidConfigError(`Registry root payload files must be an array: ${sourcePath}`)
	}
	const payload: RegistryRootPayload = {
		schema: REGISTRY_ROOT_SCHEMA,
		schema_version: REGISTRY_ROOT_SCHEMA_VERSION,
		revision,
		published_at: ensureStringField(value, "published_at", sourcePath),
		current,
		manifest,
		files: sortedRegistryRootFiles(filesValue.map((file) => parseRegistryRootFileDigest(file, sourcePath))),
	}
	validateRegistryRootPayloadBindings(payload, sourcePath)
	return payload
}

function validateRegistryRootPayloadBindings(payload: RegistryRootPayload, sourcePath: string): void {
	if (payload.current.revision !== payload.revision) {
		throw new InvalidConfigError(`Registry root current revision does not match payload revision: ${sourcePath}`)
	}
	if (payload.current.manifest_sha256 !== payload.manifest.sha256) {
		throw new InvalidConfigError(`Registry root current manifest digest does not match manifest binding: ${sourcePath}`)
	}
	if (payload.manifest.path !== snapshotRegistryPath(payload.revision, "manifest.json")) {
		throw new InvalidConfigError(`Registry root manifest path does not match payload revision: ${sourcePath}`)
	}
	for (const file of payload.files) {
		const relativePath = snapshotRelativePath(payload.revision, file.path)
		if (file.role === "authoring" && !relativePath.startsWith("authoring/")) {
			throw new InvalidConfigError(`Registry root authoring file path does not match file role: ${sourcePath}`)
		}
		if (file.role === "resolved" && !relativePath.startsWith("resolved/")) {
			throw new InvalidConfigError(`Registry root resolved file path does not match file role: ${sourcePath}`)
		}
	}
}

function parseRegistryRootCurrent(value: JsonObject, sourcePath: string): RegistryRootCurrentBinding {
	const pathValue = ensureStringField(value, "path", sourcePath)
	if (pathValue !== "current.json") {
		throw new InvalidConfigError(`Registry root current binding must point to current.json: ${sourcePath}`)
	}
	const revision = ensureStringField(value, "revision", sourcePath)
	validateRevision(revision)
	const manifestSha256 = ensureStringField(value, "manifest_sha256", sourcePath)
	validateSha256(manifestSha256, "registry root current manifest")
	const sha256 = ensureStringField(value, "sha256", sourcePath)
	validateSha256(sha256, "registry root current binding")
	return { path: "current.json", revision, manifest_sha256: manifestSha256, sha256 }
}

function parseRegistryRootManifest(value: JsonObject, sourcePath: string): RegistryRootManifestBinding {
	const sha256 = ensureStringField(value, "sha256", sourcePath)
	validateSha256(sha256, `registry root manifest ${ensureStringField(value, "path", sourcePath)}`)
	return {
		path: ensureStringField(value, "path", sourcePath),
		sha256,
	}
}

function parseRegistryRootIntegrity(value: JsonObject, sourcePath: string): RegistryRootIntegrity {
	const algorithm = ensureStringField(value, "algorithm", sourcePath)
	if (algorithm !== "sha256") {
		throw new InvalidConfigError(`Registry root integrity must use sha256: ${sourcePath}`)
	}
	const digest = ensureStringField(value, "digest", sourcePath)
	if (!digest.startsWith("sha256:")) {
		throw new InvalidConfigError(`Registry root integrity digest must be sha256-prefixed: ${sourcePath}`)
	}
	validateSha256(digest.slice("sha256:".length), "registry root integrity")
	return { algorithm, digest }
}

function parseRegistryRootSignature(value: unknown, sourcePath: string): RegistryRootSignature {
	if (!isJsonObject(value)) {
		throw new InvalidConfigError(`Registry root signature must be an object: ${sourcePath}`)
	}
	const algorithm = ensureStringField(value, "algorithm", sourcePath)
	if (algorithm !== "ed25519" && algorithm !== "ecdsa-p256" && algorithm !== "rsa-pss-sha256") {
		throw new InvalidConfigError(`Registry root signature has unsupported algorithm: ${sourcePath}`)
	}
	const payloadType = ensureStringField(value, "payload-type", sourcePath)
	if (payloadType !== REGISTRY_ROOT_PAYLOAD_TYPE) {
		throw new InvalidConfigError(`Registry root signature payload-type mismatch: ${sourcePath}`)
	}
	const signature: RegistryRootSignature = {
		signer: ensureStringField(value, "signer", sourcePath),
		"key-id": ensureStringField(value, "key-id", sourcePath),
		"payload-digest": ensureStringField(value, "payload-digest", sourcePath),
		algorithm,
		signature: ensureStringField(value, "signature", sourcePath),
		"payload-type": payloadType,
	}
	const rekorLogId = value["rekor-log-id"]
	if (rekorLogId !== undefined) {
		if (typeof rekorLogId !== "string") {
			throw new InvalidConfigError(`Registry root signature rekor-log-id must be a string: ${sourcePath}`)
		}
		signature["rekor-log-id"] = rekorLogId
	}
	const rekorLogIndex = value["rekor-log-index"]
	if (rekorLogIndex !== undefined) {
		if (typeof rekorLogIndex !== "number") {
			throw new InvalidConfigError(`Registry root signature rekor-log-index must be a number: ${sourcePath}`)
		}
		signature["rekor-log-index"] = rekorLogIndex
	}
	return signature
}

export function parseRegistryRootEnvelope(value: JsonObject, sourcePath: string): RegistryRootEnvelope {
	if (ensureStringField(value, "schema", sourcePath) !== REGISTRY_ROOT_ENVELOPE_SCHEMA) {
		throw new InvalidConfigError(`Unsupported registry root envelope schema in ${sourcePath}`)
	}
	if (ensureNumberField(value, "schema_version", sourcePath) !== REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION) {
		throw new InvalidConfigError(`Unsupported registry root envelope schema version in ${sourcePath}`)
	}
	const payload = parseRegistryRootPayload(ensureObjectField(value, "payload", sourcePath), sourcePath)
	const integrity = parseRegistryRootIntegrity(ensureObjectField(value, "integrity", sourcePath), sourcePath)
	const payloadSha256 = ensureStringField(value, "payload_sha256", sourcePath)
	validateSha256(payloadSha256, "registry root payload")
	const signaturesValue = value["signatures"]
	if (!Array.isArray(signaturesValue)) {
		throw new InvalidConfigError(`Registry root envelope signatures must be an array: ${sourcePath}`)
	}
	if (integrity.digest !== `sha256:${payloadSha256}`) {
		throw new InvalidConfigError(`Registry root integrity digest does not match payload_sha256: ${sourcePath}`)
	}
	return {
		schema: REGISTRY_ROOT_ENVELOPE_SCHEMA,
		schema_version: REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
		payload,
		integrity,
		payload_sha256: payloadSha256,
		signatures: signaturesValue.map((signature) => parseRegistryRootSignature(signature, sourcePath)),
	}
}

export async function createRegistryRootEnvelope(
	payload: RegistryRootPayload,
	options: RegistryRootSigningOptions,
): Promise<RegistryRootEnvelope> {
	const signingInput = registryRootSigningInput(payload)
	const signed = await options.signer(signingInput)
	const signatures = (Array.isArray(signed) ? signed : [signed]).map(normalizeRegistryRootSignature)
	const minSignatures = requiredSignatureCount(options.minSignatures)
	if (signatures.length < minSignatures) {
		throw new InvalidConfigError(`Registry root signer returned ${signatures.length} signatures; expected at least ${minSignatures}`)
	}
	return {
		schema: REGISTRY_ROOT_ENVELOPE_SCHEMA,
		schema_version: REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
		payload,
		integrity: signingInput.integrity,
		payload_sha256: signingInput.payloadSha256,
		signatures,
	}
}

export async function verifyRegistryRootSignatures(
	envelope: RegistryRootEnvelope,
	options: RegistryRootVerificationOptions,
): Promise<void> {
	const signingInput = registryRootSigningInputForEnvelope(envelope)
	const verificationHooks: {
		rekorClient?: RekorClient
		sigstoreVerifier?: SigstoreVerifier
		didWebVerifier?: DidWebVerifier
		payloadBytes?: Uint8Array
	} = {}
	if (options.rekorClient !== undefined) {
		verificationHooks.rekorClient = options.rekorClient
	}
	if (options.sigstoreVerifier !== undefined) {
		verificationHooks.sigstoreVerifier = options.sigstoreVerifier
	}
	if (options.didWebVerifier !== undefined) {
		verificationHooks.didWebVerifier = options.didWebVerifier
	}
	verificationHooks.payloadBytes = new TextEncoder().encode(signingInput.canonicalPayload)
	await verifySignatures(envelope.signatures, envelope.integrity, options.trustPolicy, verificationHooks)
}

export async function enforceRegistryRootFreshness(
	envelope: RegistryRootEnvelope,
	options: RegistryRootVerificationOptions,
	rootDigest?: string,
): Promise<void> {
	const payload = envelope.payload
	if (options.expectedRevision !== undefined && payload.revision !== options.expectedRevision) {
		throw new SecurityError(`Registry root revision mismatch: expected ${options.expectedRevision}, got ${payload.revision}`)
	}
	if (options.minimumRevision !== undefined && compareRevision(payload.revision, options.minimumRevision) < 0) {
		throw new SecurityError(`Registry root revision ${payload.revision} is older than minimum ${options.minimumRevision}`)
	}
	if (options.minimumPublishedAt !== undefined) {
		enforceMinimumPublishedAt(payload.published_at, options.minimumPublishedAt)
	}
	if (options.expectedRootDigest !== undefined) {
		const expectedRootDigest = normalizeSha256Digest(options.expectedRootDigest, "expected registry root digest")
		if (rootDigest !== expectedRootDigest) {
			throw new SecurityError("Registry root digest does not match expectedRootDigest")
		}
	}
	if (options.highWatermark !== undefined) {
		await enforceHighWatermark(envelope, options.highWatermark)
	}
}

function enforceMinimumPublishedAt(publishedAt: string, minimumPublishedAt: string | Date): void {
	const actual = Date.parse(publishedAt)
	const minimum = minimumPublishedAt instanceof Date ? minimumPublishedAt.getTime() : Date.parse(minimumPublishedAt)
	if (!Number.isFinite(actual) || !Number.isFinite(minimum)) {
		throw new SecurityError("Registry root published_at freshness values must be valid dates")
	}
	if (actual < minimum) {
		throw new SecurityError(`Registry root published_at ${publishedAt} is older than minimum ${new Date(minimum).toISOString()}`)
	}
}

async function enforceHighWatermark(
	envelope: RegistryRootEnvelope,
	highWatermark: RegistryRootHighWatermark,
): Promise<void> {
	const signingInput = registryRootSigningInputForEnvelope(envelope)
	if (!(await highWatermark({ envelope, ...signingInput }))) {
		throw new SecurityError("Registry root rejected by high-watermark policy")
	}
}
