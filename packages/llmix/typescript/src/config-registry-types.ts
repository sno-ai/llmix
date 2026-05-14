import type { DidWebVerifier, IntegrityField, RekorClient, SignatureEntry, SigstoreVerifier, TrustPolicy } from "@snoai/mda-config"

import type { MdaConfigLoadOptions } from "./mda-loader.js"

export const MANIFEST_SCHEMA_VERSION = 1
export const REGISTRY_ROOT_SCHEMA = "llmix.config-registry.root"
export const REGISTRY_ROOT_SCHEMA_VERSION = 1
export const REGISTRY_ROOT_ENVELOPE_SCHEMA = "llmix.config-registry.root-envelope"
export const REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION = 1
export const REGISTRY_ROOT_PAYLOAD_TYPE = "application/vnd.snoai.llmix.registry-root+json"
export const REGISTRY_ROOT_FILENAME = "registry-root.json"
export const REVISION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
export const REVISION_TOKEN_PATTERN = /\d+|\D+/g
export const DIGIT_TOKEN_PATTERN = /^\d+$/
export const SHA256_PATTERN = /^[a-f0-9]{64}$/

export type ConfigRegistryJsonValue = null | boolean | number | string | ConfigRegistryJsonValue[] | ConfigRegistryJsonObject

export interface ConfigRegistryJsonObject {
	[key: string]: ConfigRegistryJsonValue
}

export type JsonValue = ConfigRegistryJsonValue
export interface JsonObject extends ConfigRegistryJsonObject {}

export interface PresetSource {
	module: string
	preset: string
	presetId: string
	sourcePath: string
}

export interface ManifestPresetEntry {
	source_path: string
	source_sha256: string
	resolved_path: string
	resolved_sha256: string
}

export interface RegistryManifest {
	revision: string
	published_at: string
	schema_version: number
	presets: Record<string, ManifestPresetEntry>
}

export interface CurrentPointer {
	revision: string
	manifestSha256: string
}

export interface ParsedCurrentPointer {
	revision: string
	manifestSha256: string | null
}

export interface PublishedRevision {
	revision: string
	compiledPath: string
	manifestPath: string
	manifestSha256: string
	registryRootPath?: string
	registryRootSha256?: string
	activated: boolean
	presetIds: string[]
}

export interface RegistryRootCurrentBinding extends ConfigRegistryJsonObject {
	path: "current.json"
	revision: string
	manifest_sha256: string
	sha256: string
}

export interface RegistryRootManifestBinding extends ConfigRegistryJsonObject {
	path: string
	sha256: string
}

export interface RegistryRootFileDigest extends ConfigRegistryJsonObject {
	path: string
	sha256: string
	role: "source" | "resolved"
}

export interface RegistryRootPayload extends ConfigRegistryJsonObject {
	schema: typeof REGISTRY_ROOT_SCHEMA
	schema_version: typeof REGISTRY_ROOT_SCHEMA_VERSION
	revision: string
	published_at: string
	current: RegistryRootCurrentBinding
	manifest: RegistryRootManifestBinding
	files: RegistryRootFileDigest[]
}

export interface RegistryRootIntegrity extends IntegrityField, ConfigRegistryJsonObject {
	algorithm: "sha256"
	digest: string
}

export interface RegistryRootSignature extends SignatureEntry, ConfigRegistryJsonObject {
	signer: string
	"key-id": string
	"payload-digest": string
	algorithm: "ed25519" | "ecdsa-p256" | "rsa-pss-sha256"
	signature: string
	"rekor-log-id"?: string
	"rekor-log-index"?: number
	"payload-type": string
}

export interface RegistryRootEnvelope extends ConfigRegistryJsonObject {
	schema: typeof REGISTRY_ROOT_ENVELOPE_SCHEMA
	schema_version: typeof REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION
	payload: RegistryRootPayload
	integrity: RegistryRootIntegrity
	payload_sha256: string
	signatures: RegistryRootSignature[]
}

export interface RegistryRootSigningInput {
	payload: RegistryRootPayload
	canonicalPayload: string
	integrity: RegistryRootIntegrity
	payloadType: typeof REGISTRY_ROOT_PAYLOAD_TYPE
	payloadSha256: string
}

export type RegistryRootSigner = (
	input: RegistryRootSigningInput,
) => Promise<RegistryRootSignature | readonly RegistryRootSignature[]> | RegistryRootSignature | readonly RegistryRootSignature[]

export interface RegistryRootSigningOptions {
	signer: RegistryRootSigner
	minSignatures?: number
}

export interface RegistryRootFreshnessInput extends RegistryRootSigningInput {
	envelope: RegistryRootEnvelope
}

export type RegistryRootHighWatermark = (
	input: RegistryRootFreshnessInput,
) => Promise<boolean> | boolean

export interface RegistryRootVerificationOptions {
	trustPolicy: TrustPolicy
	rekorClient?: RekorClient
	sigstoreVerifier?: SigstoreVerifier
	didWebVerifier?: DidWebVerifier
	expectedRevision?: string
	expectedRootDigest?: string
	minimumRevision?: string
	minimumPublishedAt?: string | Date
	highWatermark?: RegistryRootHighWatermark
}

export interface ConfigRegistryOpenOptions {
	signedRoot?: RegistryRootVerificationOptions
}

export interface ConfigRegistryPublishOptions extends MdaConfigLoadOptions {
	revision?: string
	activate?: boolean
	registryRoot?: RegistryRootSigningOptions
}
