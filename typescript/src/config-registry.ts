/**
 * LLMix Config Registry
 *
 * Publishes immutable runtime snapshots from authoring YAML and serves the
 * active resolved configs through a small runtime manager.
 */

import { createHash } from "node:crypto"
import { mkdir, open, readdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises"
import path from "node:path"

import { loadConfig } from "./config.js"
import { ConfigAccessError, ConfigNotFoundError, InvalidConfigError, type LLMConfig, SecurityError } from "./types.js"
import { LLMConfigSchema, validateModule, validatePreset, verifyPathContainmentAsync } from "./yaml-loader.js"

const MANIFEST_SCHEMA_VERSION = 1
const REVISION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/

type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject

interface JsonObject {
	[key: string]: JsonValue
}

interface PresetSource {
	module: string
	preset: string
	presetId: string
	authoringPath: string
}

interface ManifestPresetEntry {
	authoring_path: string
	authoring_sha256: string
	resolved_path: string
	resolved_sha256: string
}

interface RegistryManifest {
	revision: string
	published_at: string
	schema_version: number
	presets: Record<string, ManifestPresetEntry>
}

interface CurrentPointer {
	revision: string
}

export interface PublishedRevision {
	revision: string
	snapshotPath: string
	manifestPath: string
	activated: boolean
	presetIds: string[]
}

function isJsonObject(value: unknown): value is JsonObject {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function canonicalJsonString(value: JsonValue | JsonObject): string {
	return `${JSON.stringify(sortJsonValue(value), null, 2)}\n`
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

function sha256Bytes(content: string | Uint8Array): string {
	return createHash("sha256").update(content).digest("hex")
}

async function sha256File(filePath: string): Promise<string> {
	const content = await readFileBytes(filePath)
	return sha256Bytes(content)
}

async function readFileBytes(filePath: string): Promise<Uint8Array> {
	try {
		return await readFile(filePath)
	} catch (error) {
		throw mapReadError(error, filePath)
	}
}

async function readJsonObject(filePath: string): Promise<JsonObject> {
	let content: string
	try {
		content = await readFile(filePath, "utf-8")
	} catch (error) {
		throw mapReadError(error, filePath)
	}

	let parsed: unknown
	try {
		parsed = JSON.parse(content)
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error)
		throw new InvalidConfigError(`Invalid JSON in registry file ${filePath}: ${message}`)
	}

	if (!isJsonObject(parsed)) {
		throw new InvalidConfigError(`Registry file must contain a JSON object: ${filePath}`)
	}

	return parsed
}

async function writeBytes(filePath: string, content: string | Uint8Array): Promise<void> {
	await mkdir(path.dirname(filePath), { recursive: true })
	await writeFile(filePath, content)
	await fsyncFile(filePath)
}

async function writeJson(filePath: string, value: JsonValue | JsonObject): Promise<void> {
	await writeBytes(filePath, canonicalJsonString(value))
}

async function atomicWriteJson(filePath: string, value: JsonValue | JsonObject): Promise<void> {
	await mkdir(path.dirname(filePath), { recursive: true })
	const tempPath = `${filePath}.tmp`
	await writeJson(tempPath, value)
	await rename(tempPath, filePath)
	await fsyncDir(path.dirname(filePath))
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

async function fsyncDir(dirPath: string): Promise<void> {
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

function mapReadError(error: unknown, filePath: string): Error {
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

function validateRevision(revision: string): void {
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

function parsePresetFilename(fileName: string): string | null {
	if (fileName.endsWith(".yaml")) {
		return fileName.slice(0, -5)
	}
	if (fileName.endsWith(".yml")) {
		return fileName.slice(0, -4)
	}
	return null
}

function camelToSnakeKey(key: string): string {
	return key
		.replace(/([A-Z]+)([A-Z][a-z0-9])/g, "$1_$2")
		.replace(/([a-z0-9])([A-Z])/g, "$1_$2")
		.toLowerCase()
}

function snakeToCamelKey(key: string): string {
	return key.replace(/_([a-z0-9])/g, (_match, letter: string) => letter.toUpperCase())
}

function convertKeysToSnakeCase(value: unknown): JsonValue {
	if (Array.isArray(value)) {
		return value.map((item) => convertKeysToSnakeCase(item))
	}

	if (!isJsonObject(value)) {
		return value as JsonValue
	}

	const normalized: JsonObject = {}
	for (const [key, item] of Object.entries(value)) {
		normalized[camelToSnakeKey(key)] = convertKeysToSnakeCase(item)
	}
	return normalized
}

function convertKeysToCamelCase(value: JsonValue): unknown {
	if (Array.isArray(value)) {
		return value.map((item) => convertKeysToCamelCase(item))
	}

	if (!isJsonObject(value)) {
		return value
	}

	const normalized: Record<string, unknown> = {}
	for (const [key, item] of Object.entries(value)) {
		normalized[snakeToCamelKey(key)] = convertKeysToCamelCase(item)
	}
	return normalized
}

function validateCanonicalResolvedObject(value: JsonObject, sourcePath: string): void {
	if (typeof value["provider"] !== "string") {
		throw new InvalidConfigError(`Missing required field 'provider' in resolved config ${sourcePath}`)
	}
	if (typeof value["model"] !== "string") {
		throw new InvalidConfigError(`Missing required field 'model' in resolved config ${sourcePath}`)
	}
}

function toCanonicalResolvedConfig(config: LLMConfig): JsonObject {
	const canonical = convertKeysToSnakeCase(config)
	if (!isJsonObject(canonical)) {
		throw new InvalidConfigError("Resolved config must serialize to a JSON object")
	}
	validateCanonicalResolvedObject(canonical, "<resolved-config>")
	return canonical
}

function fromCanonicalResolvedConfig(value: JsonObject, sourcePath: string): LLMConfig {
	validateCanonicalResolvedObject(value, sourcePath)
	const normalized = convertKeysToCamelCase(value)
	const result = LLMConfigSchema.safeParse(normalized)
	if (!result.success) {
		const issues = result.error.issues.map((issue) => `  - ${issue.path.join(".")}: ${issue.message}`).join("\n")
		throw new InvalidConfigError(`Schema validation failed for ${sourcePath}:\n${issues}`)
	}
	return result.data
}

function ensureStringField(value: JsonObject, key: string, sourcePath: string): string {
	const field = value[key]
	if (typeof field !== "string") {
		throw new InvalidConfigError(`Registry file is missing string field '${key}': ${sourcePath}`)
	}
	return field
}

function ensureManifestEntryString(value: string, key: string, presetId: string, sourcePath: string): string {
	if (value.length === 0) {
		throw new InvalidConfigError(`Config Registry manifest entry is missing ${key}: ${presetId} (${sourcePath})`)
	}
	return value
}

function parseCurrentPointer(value: JsonObject, sourcePath: string): CurrentPointer {
	const revision = ensureStringField(value, "revision", sourcePath)
	validateRevision(revision)
	return { revision }
}

function manifestToJsonObject(manifest: RegistryManifest): JsonObject {
	const presets: JsonObject = {}
	for (const [presetId, entry] of Object.entries(manifest.presets)) {
		presets[presetId] = {
			authoring_path: entry.authoring_path,
			authoring_sha256: entry.authoring_sha256,
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

function parseManifestPresetEntry(value: unknown, presetId: string, sourcePath: string): ManifestPresetEntry {
	if (!isJsonObject(value)) {
		throw new InvalidConfigError(`Registry manifest entry must be an object: ${presetId} (${sourcePath})`)
	}

	return {
		authoring_path: ensureStringField(value, "authoring_path", sourcePath),
		authoring_sha256: ensureStringField(value, "authoring_sha256", sourcePath),
		resolved_path: ensureStringField(value, "resolved_path", sourcePath),
		resolved_sha256: ensureStringField(value, "resolved_sha256", sourcePath),
	}
}

function parseManifest(value: JsonObject, sourcePath: string, expectedRevision?: string): RegistryManifest {
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

function cloneConfig(config: LLMConfig): LLMConfig {
	return JSON.parse(JSON.stringify(config)) as LLMConfig
}

export class ConfigRegistryPublisher {
	readonly root: string
	readonly authoringDir: string
	readonly snapshotsDir: string
	readonly stagingDir: string
	readonly currentPath: string

	constructor(root: string) {
		this.root = path.resolve(root)
		this.authoringDir = path.join(this.root, "authoring")
		this.snapshotsDir = path.join(this.root, "snapshots")
		this.stagingDir = path.join(this.snapshotsDir, ".staging")
		this.currentPath = path.join(this.root, "current.json")
	}

	async publish(options?: { revision?: string; activate?: boolean }): Promise<PublishedRevision> {
		const presets = await this.discoverPresets()
		if (presets.length === 0) {
			throw new ConfigNotFoundError(`No authoring presets found under ${this.authoringDir}`)
		}

		const publishedAt = new Date()
		const revisionId = options?.revision ?? (await this.buildRevisionId(presets, publishedAt))
		const activate = options?.activate ?? true
		validateRevision(revisionId)

		const snapshotPath = path.join(this.snapshotsDir, revisionId)
		const stagePath = path.join(this.stagingDir, `${revisionId}.tmp`)
		const manifestPath = path.join(snapshotPath, "manifest.json")

		try {
			await stat(snapshotPath)
			throw new InvalidConfigError(`Registry revision already exists: ${revisionId}`)
		} catch (error) {
			if (!(error instanceof Error) || !("code" in error) || (error as NodeJS.ErrnoException).code !== "ENOENT") {
				throw error
			}
		}

		await rm(stagePath, { recursive: true, force: true })

		try {
			const manifest = await this.buildStagedSnapshot(stagePath, presets, revisionId, publishedAt)
			await this.verifyStagedSnapshot(stagePath, manifest)
			await mkdir(this.snapshotsDir, { recursive: true })
			await mkdir(this.stagingDir, { recursive: true })
			await rename(stagePath, snapshotPath)
			await fsyncDir(this.snapshotsDir)

			if (activate) {
				await atomicWriteJson(this.currentPath, { revision: revisionId })
			}

			return {
				revision: revisionId,
				snapshotPath,
				manifestPath,
				activated: activate,
				presetIds: Object.keys(manifest.presets).sort(),
			}
		} catch (error) {
			await rm(stagePath, { recursive: true, force: true })
			throw error
		}
	}

	private async discoverPresets(): Promise<PresetSource[]> {
		let moduleEntries: Array<{ name: string; isDirectory(): boolean }>
		try {
			moduleEntries = await readdir(this.authoringDir, { withFileTypes: true })
		} catch (error) {
			throw mapReadError(error, this.authoringDir)
		}

		const presets: PresetSource[] = []
		for (const moduleEntry of [...moduleEntries].sort((left, right) => left.name.localeCompare(right.name))) {
			if (!moduleEntry.isDirectory()) {
				continue
			}

			const moduleName = moduleEntry.name
			validateModule(moduleName)
			const modulePath = path.join(this.authoringDir, moduleName)

			const files = await readdir(modulePath, { withFileTypes: true })
			for (const fileEntry of [...files].sort((left, right) => left.name.localeCompare(right.name))) {
				if (!fileEntry.isFile()) {
					continue
				}

				const presetName = parsePresetFilename(fileEntry.name)
				if (presetName === null) {
					continue
				}

				validatePreset(presetName)

				presets.push({
					module: moduleName,
					preset: presetName,
					presetId: `${moduleName}/${presetName}`,
					authoringPath: path.join(modulePath, fileEntry.name),
				})
			}
		}

		return presets
	}

	private async buildRevisionId(presets: PresetSource[], publishedAt: Date): Promise<string> {
		const hash = createHash("sha256")
		for (const preset of presets) {
			const relativePath = path.relative(this.authoringDir, preset.authoringPath)
			hash.update(relativePath)
			hash.update("\0")
			hash.update(await readFileBytes(preset.authoringPath))
			hash.update("\0")
		}

		const timestamp = publishedAt.toISOString().replace(/:/g, "-").replace(/\.\d{3}Z$/, "Z")
		return `${timestamp}_${hash.digest("hex").slice(0, 8)}`
	}

	private async buildStagedSnapshot(
		stagePath: string,
		presets: PresetSource[],
		revisionId: string,
		publishedAt: Date,
	): Promise<RegistryManifest> {
		const manifestPresets: Record<string, ManifestPresetEntry> = {}
		await mkdir(stagePath, { recursive: true })

		for (const preset of presets) {
			const authoringBytes = await readFileBytes(preset.authoringPath)
			const resolved = await loadConfig(preset.authoringPath)
			const canonicalResolved = toCanonicalResolvedConfig(resolved)
			fromCanonicalResolvedConfig(canonicalResolved, preset.authoringPath)
			const resolvedBytes = canonicalJsonString(canonicalResolved)

			const authoringRel = path.posix.join("authoring", preset.module, `${preset.preset}.yaml`)
			const resolvedRel = path.posix.join("resolved", preset.module, `${preset.preset}.json`)

			await writeBytes(path.join(stagePath, authoringRel), authoringBytes)
			await writeBytes(path.join(stagePath, resolvedRel), resolvedBytes)

			manifestPresets[preset.presetId] = {
				authoring_path: authoringRel,
				authoring_sha256: sha256Bytes(authoringBytes),
				resolved_path: resolvedRel,
				resolved_sha256: sha256Bytes(resolvedBytes),
			}
		}

		const manifest: RegistryManifest = {
			revision: revisionId,
			published_at: publishedAt.toISOString(),
			schema_version: MANIFEST_SCHEMA_VERSION,
			presets: manifestPresets,
		}
		await writeJson(path.join(stagePath, "manifest.json"), manifestToJsonObject(manifest))
		return manifest
	}

	private async verifyStagedSnapshot(stagePath: string, manifest: RegistryManifest): Promise<void> {
		const storedManifest = await readJsonObject(path.join(stagePath, "manifest.json"))
		if (canonicalJsonString(storedManifest) !== canonicalJsonString(manifestToJsonObject(manifest))) {
			throw new InvalidConfigError("Staged registry manifest changed during verification")
		}

		for (const [presetId, entry] of Object.entries(manifest.presets)) {
			for (const [shaKey, pathKey] of [
				["authoring_sha256", "authoring_path"],
				["resolved_sha256", "resolved_path"],
			] as const) {
				const relativePath = entry[pathKey]
				const expectedSha = entry[shaKey]
				const artifactPath = path.join(stagePath, relativePath)
				await verifyPathContainmentAsync(artifactPath, stagePath)
				const actualSha = await sha256File(artifactPath)
				if (actualSha !== expectedSha) {
					throw new InvalidConfigError(`Checksum mismatch for staged registry artifact ${artifactPath} (${presetId})`)
				}
			}
		}
	}
}

export class ConfigRegistryManager {
	readonly root: string
	readonly snapshotsDir: string
	readonly currentPath: string

	private activeRevisionValue: string | null = null
	private configs = new Map<string, LLMConfig>()
	private lastReloadErrorValue: Error | null = null
	private lastSuccessfulReloadAtValue: Date | null = null
	private lastReloadFailureAtValue: Date | null = null
	private refreshPromise: Promise<void> | null = null

	private constructor(root: string) {
		this.root = path.resolve(root)
		this.snapshotsDir = path.join(this.root, "snapshots")
		this.currentPath = path.join(this.root, "current.json")
	}

	static async open(root: string): Promise<ConfigRegistryManager> {
		const manager = new ConfigRegistryManager(root)
		await manager.loadInitialRevision()
		return manager
	}

	get activeRevision(): string {
		if (this.activeRevisionValue === null) {
			throw new InvalidConfigError("Config Registry manager is not initialized")
		}
		return this.activeRevisionValue
	}

	get lastReloadError(): Error | null {
		return this.lastReloadErrorValue
	}

	get lastSuccessfulReloadAt(): Date | null {
		return this.lastSuccessfulReloadAtValue
	}

	get lastReloadFailureAt(): Date | null {
		return this.lastReloadFailureAtValue
	}

	availablePresets(): string[] {
		return [...this.configs.keys()].sort()
	}

	async getPreset(module: string, preset: string): Promise<LLMConfig> {
		validateModule(module)
		validatePreset(preset)

		await this.refreshIfNeeded()
		const presetId = `${module}/${preset}`
		const config = this.configs.get(presetId)
		if (config === undefined) {
			throw new ConfigNotFoundError(`Preset not found in active Config Registry revision ${this.activeRevision}: ${presetId}`)
		}
		return cloneConfig(config)
	}

	private async loadInitialRevision(): Promise<void> {
		const revision = await this.readCurrentRevision()
		const configs = await this.loadRevision(revision)
		this.activeRevisionValue = revision
		this.configs = configs
		this.lastReloadErrorValue = null
		this.lastSuccessfulReloadAtValue = new Date()
		this.lastReloadFailureAtValue = null
	}

	private async refreshIfNeeded(): Promise<void> {
		let currentRevision: string
		try {
			currentRevision = await this.readCurrentRevision()
		} catch (error) {
			this.recordReloadError(error)
			return
		}

		if (currentRevision === this.activeRevisionValue) {
			return
		}

		if (this.refreshPromise === null) {
			this.refreshPromise = this.performRefresh(currentRevision).finally(() => {
				this.refreshPromise = null
			})
		}

		await this.refreshPromise
	}

	private async performRefresh(candidateRevision: string): Promise<void> {
		try {
			const latestRevision = await this.readCurrentRevision()
			if (latestRevision === this.activeRevisionValue) {
				return
			}
			if (latestRevision !== candidateRevision) {
				validateRevision(latestRevision)
			}

			const configs = await this.loadRevision(latestRevision)
			this.activeRevisionValue = latestRevision
			this.configs = configs
			this.lastReloadErrorValue = null
			this.lastSuccessfulReloadAtValue = new Date()
			this.lastReloadFailureAtValue = null
		} catch (error) {
			this.recordReloadError(error)
		}
	}

	private async readCurrentRevision(): Promise<string> {
		const pointer = parseCurrentPointer(await readJsonObject(this.currentPath), this.currentPath)
		return pointer.revision
	}

	private async loadRevision(revision: string): Promise<Map<string, LLMConfig>> {
		validateRevision(revision)
		const snapshotPath = path.join(this.snapshotsDir, revision)
		try {
			await stat(snapshotPath)
		} catch (error) {
			if (error instanceof Error && "code" in error && (error as NodeJS.ErrnoException).code === "ENOENT") {
				throw new ConfigNotFoundError(`Config Registry snapshot not found: ${snapshotPath}`)
			}
			throw error
		}

		const manifestPath = path.join(snapshotPath, "manifest.json")
		const manifest = parseManifest(await readJsonObject(manifestPath), manifestPath, revision)
		const configs = new Map<string, LLMConfig>()

		for (const [presetId, entry] of Object.entries(manifest.presets)) {
			const resolvedPath = await this.resolveSnapshotArtifact(snapshotPath, entry.resolved_path, presetId)
			ensureManifestEntryString(entry.authoring_path, "authoring_path", presetId, manifestPath)
			ensureManifestEntryString(entry.authoring_sha256, "authoring_sha256", presetId, manifestPath)
			await this.verifySnapshotChecksum(resolvedPath, entry.resolved_sha256, presetId)
			const resolved = fromCanonicalResolvedConfig(await readJsonObject(resolvedPath), resolvedPath)
			configs.set(presetId, resolved)
		}

		return configs
	}

	private async resolveSnapshotArtifact(snapshotPath: string, relativePath: string, presetId: string): Promise<string> {
		if (!relativePath) {
			throw new InvalidConfigError(`Config Registry manifest entry is missing artifact path: ${presetId}`)
		}
		const artifactPath = path.join(snapshotPath, relativePath)
		await verifyPathContainmentAsync(artifactPath, snapshotPath)
		return artifactPath
	}

	private async verifySnapshotChecksum(artifactPath: string, expectedSha: string, presetId: string): Promise<void> {
		if (!expectedSha) {
			throw new InvalidConfigError(`Config Registry manifest entry is missing checksum: ${presetId}`)
		}
		const actualSha = await sha256File(artifactPath)
		if (actualSha !== expectedSha) {
			throw new InvalidConfigError(`Checksum mismatch for Config Registry artifact ${artifactPath}`)
		}
	}

	private recordReloadError(error: unknown): void {
		this.lastReloadErrorValue = error instanceof Error ? error : new Error(String(error))
		this.lastReloadFailureAtValue = new Date()
	}
}
