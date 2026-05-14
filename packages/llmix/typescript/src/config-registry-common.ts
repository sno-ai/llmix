import { createHash, randomUUID } from "node:crypto"
import { mkdir, open, readFile, rename, rm, writeFile } from "node:fs/promises"
import path from "node:path"
import { TextDecoder } from "node:util"

import type { LLMConfig } from "./types.js"
import { ConfigAccessError, ConfigNotFoundError, InvalidConfigError, SecurityError } from "./types.js"
import { LLMConfigSchema, type MdaConfigLoadOptions } from "./mda-loader.js"
import {
	MANIFEST_SCHEMA_VERSION,
	REVISION_PATTERN,
	REVISION_TOKEN_PATTERN,
	DIGIT_TOKEN_PATTERN,
	SHA256_PATTERN,
	type ConfigRegistryPublishOptions,
	type CurrentPointer,
	type JsonObject,
	type JsonValue,
	type ManifestPresetEntry,
	type ParsedCurrentPointer,
	type RegistryManifest,
} from "./config-registry-types.js"

export function isJsonObject(value: unknown): value is JsonObject {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

export function canonicalJsonString(value: JsonValue | JsonObject): string {
	return `${JSON.stringify(sortJsonValue(value), null, 2)}\n`
}

export function canonicalCompactJsonString(value: JsonValue | JsonObject): string {
	return JSON.stringify(sortJsonValue(value))
}

function sortJsonValue(value: JsonValue | JsonObject): JsonValue | JsonObject {
	if (Array.isArray(value)) {
		return value.map((item) => sortJsonValue(item as JsonValue))
	}

	if (!isJsonObject(value)) {
		return value as JsonValue
	}

	const sorted: JsonObject = {}
	for (const [key, item] of Object.entries(value).sort(([left], [right]) => left.localeCompare(right))) {
		sorted[key] = sortJsonValue(item)
	}
	return sorted
}

export function sha256Bytes(content: string | Uint8Array): string {
	return createHash("sha256").update(content).digest("hex")
}

export async function sha256File(filePath: string): Promise<string> {
	const content = await readFileBytes(filePath)
	return sha256Bytes(content)
}

export function validateSha256(sha256: string, label: string): void {
	if (!SHA256_PATTERN.test(sha256)) {
		throw new InvalidConfigError(`Invalid SHA-256 digest for ${label}`)
	}
}

export function normalizeSha256Digest(digest: string, label: string): string {
	const normalized = digest.startsWith("sha256:") ? digest.slice("sha256:".length) : digest
	validateSha256(normalized, label)
	return normalized
}

function currentPointerToJson(pointer: CurrentPointer): JsonObject {
	return {
		revision: pointer.revision,
		manifest_sha256: pointer.manifestSha256,
	}
}

export function currentPointerSha256(pointer: CurrentPointer): string {
	return sha256Bytes(canonicalJsonString(currentPointerToJson(pointer)))
}

export function compiledRegistryPath(revision: string, relativePath: string): string {
	validateRevision(revision)
	validateRegistryArtifactPath(relativePath, "compiled revision path", "<registry>")
	return path.posix.join("compiled", revision, relativePath)
}

export function compiledRelativePath(revision: string, registryPath: string): string {
	validateRevision(revision)
	const prefix = `compiled/${revision}/`
	if (!registryPath.startsWith(prefix)) {
		throw new SecurityError(`Registry root file is outside the active compiled revision: ${registryPath}`)
	}
	const relativePath = registryPath.slice(prefix.length)
	if (!relativePath) {
		throw new SecurityError(`Registry root file path is not a compiled revision artifact: ${registryPath}`)
	}
	validateRegistryArtifactPath(relativePath, "registry root file", registryPath)
	return relativePath
}

export async function readFileBytes(filePath: string): Promise<Uint8Array> {
	try {
		return await readFile(filePath)
	} catch (error) {
		throw mapReadError(error, filePath)
	}
}

export async function readJsonObject(filePath: string): Promise<JsonObject> {
	let content: string
	try {
		content = await readFile(filePath, "utf-8")
	} catch (error) {
		throw mapReadError(error, filePath)
	}

	return parseJsonObjectBytes(content, filePath)
}

export function parseJsonObjectBytes(content: string | Uint8Array, filePath: string): JsonObject {
	const text = typeof content === "string" ? content : new TextDecoder().decode(content)
	let parsed: unknown
	try {
		parsed = JSON.parse(text)
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error)
		throw new InvalidConfigError(`Invalid JSON in registry file ${filePath}: ${message}`)
	}

	if (!isJsonObject(parsed)) {
		throw new InvalidConfigError(`Registry file must contain a JSON object: ${filePath}`)
	}

	return parsed
}

export async function writeBytes(filePath: string, content: string | Uint8Array): Promise<void> {
	await mkdir(path.dirname(filePath), { recursive: true })
	await writeFile(filePath, content)
	await fsyncFile(filePath)
}

export async function writeJson(filePath: string, value: JsonValue | JsonObject): Promise<void> {
	await writeBytes(filePath, canonicalJsonString(value))
}

export async function atomicWriteJson(filePath: string, value: JsonValue | JsonObject): Promise<void> {
	await mkdir(path.dirname(filePath), { recursive: true })
	const tempPath = uniqueTempPath(filePath)
	try {
		await writeJson(tempPath, value)
		await rename(tempPath, filePath)
		await fsyncDir(path.dirname(filePath))
	} catch (error) {
		await rm(tempPath, { force: true })
		throw error
	}
}

function uniqueTempPath(filePath: string): string {
	return `${filePath}.${process.pid}.${randomUUID()}.tmp`
}

async function fsyncFile(filePath: string): Promise<void> {
	try {
		const handle = await open(filePath, "r")
		try {
			await handle.sync()
		} finally {
			await handle.close()
		}
	} catch {
		return
	}
}

export async function fsyncDir(dirPath: string): Promise<void> {
	try {
		const handle = await open(dirPath, "r")
		try {
			await handle.sync()
		} finally {
			await handle.close()
		}
	} catch {
		return
	}
}

export function mapReadError(error: unknown, filePath: string): Error {
	if (error instanceof Error && "code" in error) {
		const code = (error as NodeJS.ErrnoException).code
		if (code === "ENOENT") {
			return new ConfigNotFoundError(`Required registry file not found: ${filePath}`)
		}
		if (code === "EACCES") {
			return new ConfigAccessError(`Permission denied reading registry file: ${filePath}`)
		}
	}

	return error instanceof Error ? error : new Error(String(error))
}

export function validateRevision(revision: string): void {
	if (!revision) {
		throw new InvalidConfigError("Registry revision cannot be empty")
	}
	if (revision.includes("/") || revision.includes("\\") || revision.includes("..")) {
		throw new SecurityError(`Invalid registry revision: ${JSON.stringify(revision)}`)
	}
	if (!REVISION_PATTERN.test(revision)) {
		throw new InvalidConfigError(`Invalid registry revision format: ${JSON.stringify(revision)}`)
	}
}

export function compareRevision(left: string, right: string): number {
	validateRevision(left)
	validateRevision(right)
	const leftTokens = left.match(REVISION_TOKEN_PATTERN) ?? []
	const rightTokens = right.match(REVISION_TOKEN_PATTERN) ?? []
	const length = Math.max(leftTokens.length, rightTokens.length)
	for (let index = 0; index < length; index++) {
		const leftToken = leftTokens[index]
		const rightToken = rightTokens[index]
		if (leftToken === undefined) {
			return -1
		}
		if (rightToken === undefined) {
			return 1
		}
		const comparison =
			DIGIT_TOKEN_PATTERN.test(leftToken) && DIGIT_TOKEN_PATTERN.test(rightToken)
				? compareNumericRevisionToken(leftToken, rightToken)
				: compareAscii(leftToken, rightToken)
		if (comparison !== 0) {
			return comparison
		}
	}
	return 0
}

function compareNumericRevisionToken(left: string, right: string): number {
	const normalizedLeft = normalizeNumericRevisionToken(left)
	const normalizedRight = normalizeNumericRevisionToken(right)
	if (normalizedLeft.length !== normalizedRight.length) {
		return normalizedLeft.length < normalizedRight.length ? -1 : 1
	}
	return compareAscii(normalizedLeft, normalizedRight)
}

function normalizeNumericRevisionToken(token: string): string {
	const normalized = token.replace(/^0+/, "")
	return normalized === "" ? "0" : normalized
}

function compareAscii(left: string, right: string): number {
	if (left === right) {
		return 0
	}
	return left < right ? -1 : 1
}

export function isLegacyYamlPresetFilename(fileName: string): boolean {
	const lowerName = fileName.toLowerCase()
	return lowerName.endsWith(".yaml") || lowerName.endsWith(".yml")
}

export function parseMdaPresetFilename(fileName: string): string | null {
	return fileName.toLowerCase().endsWith(".mda") ? fileName.slice(0, -4) : null
}

export function toMdaConfigLoadOptions(options?: ConfigRegistryPublishOptions): MdaConfigLoadOptions | undefined {
	if (options === undefined) {
		return undefined
	}

	const loadOptions: MdaConfigLoadOptions = {}
	if (options.verifyIntegrity !== undefined) {
		loadOptions.verifyIntegrity = options.verifyIntegrity
	}
	if (options.verifySignatures !== undefined) {
		loadOptions.verifySignatures = options.verifySignatures
	}
	if (options.trustedRuntime !== undefined) {
		loadOptions.trustedRuntime = options.trustedRuntime
	}
	if (options.enforceRequires !== undefined) {
		loadOptions.enforceRequires = options.enforceRequires
	}
	if (options.allowedNetworks !== undefined) {
		loadOptions.allowedNetworks = options.allowedNetworks
	}
	if (options.trustPolicy !== undefined) {
		loadOptions.trustPolicy = options.trustPolicy
	}
	if (options.rekorClient !== undefined) {
		loadOptions.rekorClient = options.rekorClient
	}
	if (options.sigstoreVerifier !== undefined) {
		loadOptions.sigstoreVerifier = options.sigstoreVerifier
	}
	if (options.didWebVerifier !== undefined) {
		loadOptions.didWebVerifier = options.didWebVerifier
	}

	return Object.keys(loadOptions).length === 0 ? undefined : loadOptions
}

export function legacyYamlSourceError(filePath: string): InvalidConfigError {
	return new InvalidConfigError(`Legacy YAML source presets are not supported; use .mda: ${filePath}`)
}

function validateCanonicalResolvedObject(value: JsonObject, sourcePath: string): void {
	if (typeof value["provider"] !== "string") {
		throw new InvalidConfigError(`Missing required field 'provider' in resolved config ${sourcePath}`)
	}
	if (typeof value["model"] !== "string") {
		throw new InvalidConfigError(`Missing required field 'model' in resolved config ${sourcePath}`)
	}
}

export function toCanonicalResolvedConfig(config: LLMConfig): JsonObject {
	const canonical = JSON.parse(JSON.stringify(config)) as unknown
	if (!isJsonObject(canonical)) {
		throw new InvalidConfigError("Resolved config must serialize to a JSON object")
	}
	validateCanonicalResolvedObject(canonical, "<resolved-config>")
	return canonical
}

export function fromCanonicalResolvedConfig(value: JsonObject, sourcePath: string): LLMConfig {
	validateCanonicalResolvedObject(value, sourcePath)
	const result = LLMConfigSchema.safeParse(value)
	if (!result.success) {
		const issues = result.error.issues.map((issue) => `  - ${issue.path.join(".")}: ${issue.message}`).join("\n")
		throw new InvalidConfigError(`Schema validation failed for ${sourcePath}:\n${issues}`)
	}
	return result.data
}

export function ensureStringField(value: JsonObject, key: string, sourcePath: string): string {
	const field = value[key]
	if (typeof field !== "string") {
		throw new InvalidConfigError(`Registry file is missing string field '${key}': ${sourcePath}`)
	}
	return field
}

export function ensureNumberField(value: JsonObject, key: string, sourcePath: string): number {
	const field = value[key]
	if (typeof field !== "number") {
		throw new InvalidConfigError(`Registry file is missing number field '${key}': ${sourcePath}`)
	}
	return field
}

export function ensureObjectField(value: JsonObject, key: string, sourcePath: string): JsonObject {
	const field = value[key]
	if (!isJsonObject(field)) {
		throw new InvalidConfigError(`Registry file is missing object field '${key}': ${sourcePath}`)
	}
	return field
}

function validateManifestSha256(manifestSha256: string, sourcePath: string): void {
	if (!SHA256_PATTERN.test(manifestSha256)) {
		throw new InvalidConfigError(`Registry current pointer has invalid manifest_sha256: ${sourcePath}`)
	}
}

export function parseCurrentPointer(value: JsonObject, sourcePath: string): ParsedCurrentPointer {
	const revision = ensureStringField(value, "revision", sourcePath)
	validateRevision(revision)
	const manifestSha256Value = value["manifest_sha256"]
	if (manifestSha256Value === undefined) {
		return { revision, manifestSha256: null }
	}
	if (typeof manifestSha256Value !== "string") {
		throw new InvalidConfigError(`Registry file is missing string field 'manifest_sha256': ${sourcePath}`)
	}
	const manifestSha256 = manifestSha256Value
	validateManifestSha256(manifestSha256, sourcePath)
	return { revision, manifestSha256 }
}

export function manifestToJsonObject(manifest: RegistryManifest): JsonObject {
	const presets: JsonObject = {}
	for (const [presetId, entry] of Object.entries(manifest.presets)) {
		presets[presetId] = {
			source_path: entry.source_path,
			source_sha256: entry.source_sha256,
			resolved_path: entry.resolved_path,
			resolved_sha256: entry.resolved_sha256,
		}
	}

	return {
		revision: manifest.revision,
		published_at: manifest.published_at,
		schema_version: manifest.schema_version,
		presets,
	}
}

function parseManifestPresetEntry(value: unknown, presetId: string, manifestPath: string): ManifestPresetEntry {
	if (!isJsonObject(value)) {
		throw new InvalidConfigError(`Registry manifest entry must be an object: ${presetId} (${manifestPath})`)
	}

	const sourcePath = ensureStringField(value, "source_path", manifestPath)
	const sourceSha256 = ensureStringField(value, "source_sha256", manifestPath)
	const resolvedPath = ensureStringField(value, "resolved_path", manifestPath)
	const resolvedSha256 = ensureStringField(value, "resolved_sha256", manifestPath)
	validateRegistryArtifactPath(sourcePath, `source_path for ${presetId}`, manifestPath)
	validateRegistryArtifactPath(resolvedPath, `resolved_path for ${presetId}`, manifestPath)
	validateSha256(sourceSha256, `manifest source_sha256 for ${presetId}`)
	validateSha256(resolvedSha256, `manifest resolved_sha256 for ${presetId}`)

	return {
		source_path: sourcePath,
		source_sha256: sourceSha256,
		resolved_path: resolvedPath,
		resolved_sha256: resolvedSha256,
	}
}

export function validateRegistryArtifactPath(relativePath: string, label: string, sourcePath: string): void {
	if (!relativePath) {
		throw new InvalidConfigError(`Registry manifest entry is missing artifact path ${label}: ${sourcePath}`)
	}
	if (path.posix.isAbsolute(relativePath) || path.win32.isAbsolute(relativePath) || relativePath.includes("\\")) {
		throw new InvalidConfigError(`Registry manifest artifact path must be relative POSIX path ${label}: ${sourcePath}`)
	}
	const segments = relativePath.split("/")
	if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
		throw new InvalidConfigError(`Registry manifest artifact path must not contain traversal segments ${label}: ${sourcePath}`)
	}
	if (path.posix.normalize(relativePath) !== relativePath) {
		throw new InvalidConfigError(`Registry manifest artifact path must be normalized ${label}: ${sourcePath}`)
	}
}

export function parseManifest(value: JsonObject, sourcePath: string, expectedRevision?: string): RegistryManifest {
	const revision = ensureStringField(value, "revision", sourcePath)
	validateRevision(revision)
	if (expectedRevision !== undefined && revision !== expectedRevision) {
		throw new InvalidConfigError(`Config Registry manifest revision mismatch in ${sourcePath}`)
	}

	const publishedAt = ensureStringField(value, "published_at", sourcePath)
	const schemaVersion = value["schema_version"]
	if (schemaVersion !== MANIFEST_SCHEMA_VERSION) {
		throw new InvalidConfigError(`Unsupported Config Registry manifest schema version in ${sourcePath}`)
	}

	const presetsValue = value["presets"]
	if (!isJsonObject(presetsValue)) {
		throw new InvalidConfigError(`Config Registry manifest presets index must be an object: ${sourcePath}`)
	}

	const presets: Record<string, ManifestPresetEntry> = {}
	for (const [presetId, entry] of Object.entries(presetsValue)) {
		presets[presetId] = parseManifestPresetEntry(entry, presetId, sourcePath)
	}

	return {
		revision,
		published_at: publishedAt,
		schema_version: MANIFEST_SCHEMA_VERSION,
		presets,
	}
}

export function cloneConfig(config: LLMConfig): LLMConfig {
	return JSON.parse(JSON.stringify(config)) as LLMConfig
}
