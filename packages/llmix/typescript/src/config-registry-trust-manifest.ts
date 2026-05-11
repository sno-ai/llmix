import type { DidWebVerifier, RekorClient, SigstoreVerifier, TrustPolicy } from "@snoai/mda-config"

import { InvalidConfigError } from "./types.js"
import { compareRevision, isJsonObject, normalizeSha256Digest, readJsonObject } from "./config-registry-common.js"
import type { JsonObject, JsonValue, RegistryRootHighWatermark, RegistryRootVerificationOptions } from "./config-registry-types.js"

export const LLMIX_TRUST_MANIFEST_KIND = "llmix-trust-manifest"
export const LLMIX_TRUST_MANIFEST_VERSION = 1

const DIGEST_PATTERN = /^sha256:[a-f0-9]{64}$/

export interface LlmixTrustManifestRegistryRoot {
	path: string
	revision: string
	publishedAt: string
	highWatermark: string
}

export interface LlmixTrustManifestReleasePlan {
	path: string
	sourceCount: number
}

export interface LlmixTrustManifest {
	version: typeof LLMIX_TRUST_MANIFEST_VERSION
	kind: typeof LLMIX_TRUST_MANIFEST_KIND
	expectedRootDigest: string
	sourceSetDigest: string
	releasePlanDigest: string
	registryRootTrustPolicy: TrustPolicy
	rekorPolicy: JsonObject | null
	minimumRevision: string | null
	minimumPublishedAt: string | null
	highWatermark: string | null
	registryRootSignerIdentity: JsonValue
	registryRoot: LlmixTrustManifestRegistryRoot
	releasePlan: LlmixTrustManifestReleasePlan
}

export interface LlmixTrustManifestVerificationHooks {
	rekorClient?: RekorClient
	sigstoreVerifier?: SigstoreVerifier
	didWebVerifier?: DidWebVerifier
	highWatermark?: RegistryRootHighWatermark
}

export async function loadLlmixTrustManifest(filePath: string): Promise<LlmixTrustManifest> {
	return parseLlmixTrustManifest(await readJsonObject(filePath), filePath)
}

export function parseLlmixTrustManifest(value: unknown, sourcePath = "LLMix trust manifest"): LlmixTrustManifest {
	const manifest = ensureObject(value, sourcePath)
	if (manifest["kind"] !== LLMIX_TRUST_MANIFEST_KIND) {
		throw new InvalidConfigError(`Invalid LLMix trust manifest kind in ${sourcePath}`)
	}
	if (manifest["version"] !== LLMIX_TRUST_MANIFEST_VERSION) {
		throw new InvalidConfigError(`Invalid LLMix trust manifest version in ${sourcePath}`)
	}
	const expectedRootDigest = ensureDigest(manifest, "expectedRootDigest", sourcePath)
	const sourceSetDigest = ensureDigest(manifest, "sourceSetDigest", sourcePath)
	const releasePlanDigest = ensureDigest(manifest, "releasePlanDigest", sourcePath)
	const registryRootTrustPolicy = ensureObject(
		manifest["registryRootTrustPolicy"],
		`${sourcePath}.registryRootTrustPolicy`,
	) as unknown as TrustPolicy
	const rekorPolicy = ensureNullableObject(manifest["rekorPolicy"], `${sourcePath}.rekorPolicy`)
	const registryRoot = parseRegistryRoot(manifest["registryRoot"], `${sourcePath}.registryRoot`)
	const releasePlan = parseReleasePlan(manifest["releasePlan"], `${sourcePath}.releasePlan`)
	const minimumRevision = ensureNullableString(manifest["minimumRevision"], `${sourcePath}.minimumRevision`)
	const minimumPublishedAt = ensureNullableString(manifest["minimumPublishedAt"], `${sourcePath}.minimumPublishedAt`)
	const highWatermark = ensureNullableString(manifest["highWatermark"], `${sourcePath}.highWatermark`)
	const registryRootSignerIdentity = ensureJsonValue(
		manifest["registryRootSignerIdentity"],
		`${sourcePath}.registryRootSignerIdentity`,
	)

	return {
		version: LLMIX_TRUST_MANIFEST_VERSION,
		kind: LLMIX_TRUST_MANIFEST_KIND,
		expectedRootDigest,
		sourceSetDigest,
		releasePlanDigest,
		registryRootTrustPolicy,
		rekorPolicy,
		minimumRevision,
		minimumPublishedAt,
		highWatermark,
		registryRootSignerIdentity,
		registryRoot,
		releasePlan,
	}
}

export function registryRootOptionsFromTrustManifest(
	manifest: LlmixTrustManifest,
	hooks: LlmixTrustManifestVerificationHooks = {},
): RegistryRootVerificationOptions {
	const minimumRevision = minimumRevisionFromManifest(manifest)
	return {
		trustPolicy: manifest.registryRootTrustPolicy,
		...(hooks.rekorClient === undefined ? {} : { rekorClient: hooks.rekorClient }),
		...(hooks.sigstoreVerifier === undefined ? {} : { sigstoreVerifier: hooks.sigstoreVerifier }),
		...(hooks.didWebVerifier === undefined ? {} : { didWebVerifier: hooks.didWebVerifier }),
		expectedRevision: manifest.registryRoot.revision,
		expectedRootDigest: normalizeSha256Digest(manifest.expectedRootDigest, "LLMix trust manifest expectedRootDigest"),
		...(minimumRevision === null ? {} : { minimumRevision }),
		...(manifest.minimumPublishedAt === null ? {} : { minimumPublishedAt: manifest.minimumPublishedAt }),
		...(hooks.highWatermark === undefined ? {} : { highWatermark: hooks.highWatermark }),
	}
}

function minimumRevisionFromManifest(manifest: LlmixTrustManifest): string | null {
	if (manifest.highWatermark === null) {
		return manifest.minimumRevision
	}
	if (manifest.minimumRevision === null) {
		return manifest.highWatermark
	}
	return compareRevision(manifest.minimumRevision, manifest.highWatermark) >= 0
		? manifest.minimumRevision
		: manifest.highWatermark
}

function parseRegistryRoot(value: unknown, sourcePath: string): LlmixTrustManifestRegistryRoot {
	const root = ensureObject(value, sourcePath)
	const publishedAt = ensureNonEmptyString(root["publishedAt"], `${sourcePath}.publishedAt`)
	if (!Number.isFinite(Date.parse(publishedAt))) {
		throw new InvalidConfigError(`Invalid ISO timestamp for ${sourcePath}.publishedAt`)
	}
	return {
		path: ensureNonEmptyString(root["path"], `${sourcePath}.path`),
		revision: ensureNonEmptyString(root["revision"], `${sourcePath}.revision`),
		publishedAt,
		highWatermark: ensureNonEmptyString(root["highWatermark"], `${sourcePath}.highWatermark`),
	}
}

function parseReleasePlan(value: unknown, sourcePath: string): LlmixTrustManifestReleasePlan {
	const releasePlan = ensureObject(value, sourcePath)
	const sourceCount = releasePlan["sourceCount"]
	if (typeof sourceCount !== "number" || !Number.isInteger(sourceCount) || sourceCount < 0) {
		throw new InvalidConfigError(`Invalid non-negative integer for ${sourcePath}.sourceCount`)
	}
	return {
		path: ensureNonEmptyString(releasePlan["path"], `${sourcePath}.path`),
		sourceCount,
	}
}

function ensureDigest(value: JsonObject, field: string, sourcePath: string): string {
	const digest = value[field]
	if (typeof digest !== "string" || !DIGEST_PATTERN.test(digest)) {
		throw new InvalidConfigError(`Invalid digest for ${sourcePath}.${field}`)
	}
	return digest
}

function ensureObject(value: unknown, sourcePath: string): JsonObject {
	if (!isJsonObject(value)) {
		throw new InvalidConfigError(`${sourcePath} must be a JSON object`)
	}
	return value
}

function ensureJsonValue(value: JsonValue | undefined, sourcePath: string): JsonValue {
	if (value === undefined) {
		throw new InvalidConfigError(`${sourcePath} must be present`)
	}
	return value
}

function ensureNullableObject(value: unknown, sourcePath: string): JsonObject | null {
	if (value === null) {
		return null
	}
	return ensureObject(value, sourcePath)
}

function ensureNullableString(value: unknown, sourcePath: string): string | null {
	if (value === null) {
		return null
	}
	return ensureNonEmptyString(value, sourcePath)
}

function ensureNonEmptyString(value: unknown, sourcePath: string): string {
	if (typeof value !== "string" || value.length === 0) {
		throw new InvalidConfigError(`${sourcePath} must be a non-empty string`)
	}
	return value
}
