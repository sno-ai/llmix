/**
 * LLMix Config Registry
 *
 * Publishes immutable runtime snapshots from authoring MDA and serves the
 * active resolved configs through a small runtime manager.
 */

import { createHash, randomUUID } from "node:crypto"
import { mkdir, open, readdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises"
import path from "node:path"

import { ConfigAccessError, ConfigNotFoundError, InvalidConfigError, type LLMConfig, SecurityError } from "./types.js"
import {
	LLMConfigSchema,
	loadMdaConfig,
	type MdaConfigLoadOptions,
	validateModule,
	validatePreset,
	verifyPathContainmentAsync,
} from "./mda-loader.js"

const MANIFEST_SCHEMA_VERSION = 1
const REVISION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const SHA256_PATTERN = /^[a-f0-9]{64}$/

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
	manifestSha256: string
}

interface ParsedCurrentPointer {
	revision: string
	manifestSha256: string | null
}

export interface PublishedRevision {
	revision: string
	snapshotPath: string
	manifestPath: string
	manifestSha256: string
	activated: boolean
	presetIds: string[]
}

export interface ConfigRegistryPublishOptions extends MdaConfigLoadOptions {
	revision?: string
	activate?: boolean
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

function isLegacyYamlPresetFilename(fileName: string): boolean {
	const lowerName = fileName.toLowerCase()
	return lowerName.endsWith(".yaml") || lowerName.endsWith(".yml")
}

function parseMdaPresetFilename(fileName: string): string | null {
	return fileName.toLowerCase().endsWith(".mda") ? fileName.slice(0, -4) : null
}

function toMdaConfigLoadOptions(options?: ConfigRegistryPublishOptions): MdaConfigLoadOptions | undefined {
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

	return Object.keys(loadOptions).length === 0 ? undefined : loadOptions
}

function legacyYamlAuthoringError(filePath: string): InvalidConfigError {
	return new InvalidConfigError(`Legacy YAML authoring presets are not supported; use .mda: ${filePath}`)
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
	const canonical = JSON.parse(JSON.stringify(config)) as unknown
	if (!isJsonObject(canonical)) {
		throw new InvalidConfigError("Resolved config must serialize to a JSON object")
	}
	validateCanonicalResolvedObject(canonical, "<resolved-config>")
	return canonical
}

function fromCanonicalResolvedConfig(value: JsonObject, sourcePath: string): LLMConfig {
	validateCanonicalResolvedObject(value, sourcePath)
	const result = LLMConfigSchema.safeParse(value)
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

function validateManifestSha256(manifestSha256: string, sourcePath: string): void {
	if (!SHA256_PATTERN.test(manifestSha256)) {
		throw new InvalidConfigError(`Registry current pointer has invalid manifest_sha256: ${sourcePath}`)
	}
}

function parseCurrentPointer(value: JsonObject, sourcePath: string): ParsedCurrentPointer {
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

	async publish(options?: ConfigRegistryPublishOptions): Promise<PublishedRevision> {
		const presets = await this.discoverPresets()
		if (presets.length === 0) {
			throw new ConfigNotFoundError(`No authoring presets found under ${this.authoringDir}`)
		}

		const publishedAt = new Date()
		const revisionId = options?.revision ?? (await this.buildRevisionId(presets, publishedAt))
		const activate = options?.activate ?? true
		validateRevision(revisionId)

		const snapshotPath = path.join(this.snapshotsDir, revisionId)
		const stagePath = path.join(this.stagingDir, `${revisionId}.${process.pid}.${randomUUID()}.tmp`)
		const manifestPath = path.join(snapshotPath, "manifest.json")

		try {
			await stat(snapshotPath)
			throw new InvalidConfigError(`Registry revision already exists: ${revisionId}`)
		} catch (error) {
			if (!(error instanceof Error) || !("code" in error) || (error as NodeJS.ErrnoException).code !== "ENOENT") {
				throw error
			}
		}

		try {
			const manifest = await this.buildStagedSnapshot(
				stagePath,
				presets,
				revisionId,
				publishedAt,
				toMdaConfigLoadOptions(options),
			)
			await this.verifyStagedSnapshot(stagePath, manifest)
			const manifestSha256 = await sha256File(path.join(stagePath, "manifest.json"))
			await mkdir(this.snapshotsDir, { recursive: true })
			await mkdir(this.stagingDir, { recursive: true })
			await rename(stagePath, snapshotPath)
			await fsyncDir(this.snapshotsDir)

			if (activate) {
				await atomicWriteJson(this.currentPath, { revision: revisionId, manifest_sha256: manifestSha256 })
			}

			return {
				revision: revisionId,
				snapshotPath,
				manifestPath,
				manifestSha256,
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

				const authoringPath = path.join(modulePath, fileEntry.name)
				if (isLegacyYamlPresetFilename(fileEntry.name)) {
					throw legacyYamlAuthoringError(authoringPath)
				}

				const presetName = parseMdaPresetFilename(fileEntry.name)
				if (presetName === null) {
					continue
				}

				validatePreset(presetName)

				presets.push({
					module: moduleName,
					preset: presetName,
					presetId: `${moduleName}/${presetName}`,
					authoringPath,
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
		loadOptions?: MdaConfigLoadOptions,
	): Promise<RegistryManifest> {
		const manifestPresets: Record<string, ManifestPresetEntry> = {}
		await mkdir(stagePath, { recursive: true })

		for (const preset of presets) {
			const authoringBytes = await readFileBytes(preset.authoringPath)
			const authoringRel = path.posix.join("authoring", preset.module, `${preset.preset}.mda`)
			const resolvedRel = path.posix.join("resolved", preset.module, `${preset.preset}.json`)
			const stagedAuthoringPath = path.join(stagePath, authoringRel)

			await writeBytes(stagedAuthoringPath, authoringBytes)

			const resolved = await loadMdaConfig(stagedAuthoringPath, loadOptions)
			const canonicalResolved = toCanonicalResolvedConfig(resolved)
			fromCanonicalResolvedConfig(canonicalResolved, stagedAuthoringPath)
			const resolvedBytes = canonicalJsonString(canonicalResolved)

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
	private activeManifestSha256Value: string | null = null
	private configs = new Map<string, LLMConfig>()
	private lastReloadErrorValue: Error | null = null
	private lastSuccessfulReloadAtValue: Date | null = null
	private lastReloadFailureAtValue: Date | null = null
	private refreshPromise: Promise<boolean> | null = null

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

	async availablePresets(): Promise<string[]> {
		await this.refreshIfNeeded()
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
		const pointer = await this.readCurrentPointer()
		const configs = await this.loadRevision(pointer)
		this.activeRevisionValue = pointer.revision
		this.activeManifestSha256Value = pointer.manifestSha256
		this.configs = configs
		this.lastReloadErrorValue = null
		this.lastSuccessfulReloadAtValue = new Date()
		this.lastReloadFailureAtValue = null
	}

	private async refreshIfNeeded(): Promise<void> {
		for (;;) {
			let currentPointer: CurrentPointer
			try {
				currentPointer = await this.readCurrentPointer()
			} catch (error) {
				this.recordReloadError(error)
				return
			}

			if (
				currentPointer.revision === this.activeRevisionValue &&
				currentPointer.manifestSha256 === this.activeManifestSha256Value
			) {
				return
			}

			if (this.refreshPromise === null) {
				this.refreshPromise = this.performRefresh().finally(() => {
					this.refreshPromise = null
				})
			}

			if (!(await this.refreshPromise)) {
				return
			}
		}
	}

	private async performRefresh(): Promise<boolean> {
		try {
			const latestPointer = await this.readCurrentPointer()
			if (
				latestPointer.revision === this.activeRevisionValue &&
				latestPointer.manifestSha256 === this.activeManifestSha256Value
			) {
				return true
			}

			const configs = await this.loadRevision(latestPointer)
			this.activeRevisionValue = latestPointer.revision
			this.activeManifestSha256Value = latestPointer.manifestSha256
			this.configs = configs
			this.lastReloadErrorValue = null
			this.lastSuccessfulReloadAtValue = new Date()
			this.lastReloadFailureAtValue = null
			return true
		} catch (error) {
			this.recordReloadError(error)
			return false
		}
	}

	private async readCurrentPointer(): Promise<CurrentPointer> {
		const pointer = parseCurrentPointer(await readJsonObject(this.currentPath), this.currentPath)
		if (pointer.manifestSha256 !== null) {
			return { revision: pointer.revision, manifestSha256: pointer.manifestSha256 }
		}

		const manifestSha256 = await sha256File(path.join(this.snapshotsDir, pointer.revision, "manifest.json"))
		return { revision: pointer.revision, manifestSha256 }
	}

	private async loadRevision(pointer: CurrentPointer): Promise<Map<string, LLMConfig>> {
		const revision = pointer.revision
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
		await this.verifySnapshotChecksum(manifestPath, pointer.manifestSha256, revision)
		const manifest = parseManifest(await readJsonObject(manifestPath), manifestPath, revision)
		const configs = new Map<string, LLMConfig>()

		for (const [presetId, entry] of Object.entries(manifest.presets)) {
			const authoringPath = await this.resolveSnapshotArtifact(snapshotPath, entry.authoring_path, presetId)
			await this.verifySnapshotChecksum(authoringPath, entry.authoring_sha256, presetId)
			const resolvedPath = await this.resolveSnapshotArtifact(snapshotPath, entry.resolved_path, presetId)
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
