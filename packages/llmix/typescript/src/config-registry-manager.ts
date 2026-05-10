import { stat } from "node:fs/promises"
import path from "node:path"

import { ConfigNotFoundError, InvalidConfigError, type LLMConfig, SecurityError } from "./types.js"
import { validateModule, validatePreset, verifyPathContainmentAsync } from "./mda-loader.js"
import {
	canonicalJsonString,
	cloneConfig,
	currentPointerSha256,
	fromCanonicalResolvedConfig,
	parseCurrentPointer,
	parseJsonObjectBytes,
	parseManifest,
	readFileBytes,
	readJsonObject,
	sha256Bytes,
	sha256File,
	snapshotRegistryPath,
	snapshotRelativePath,
	validateRevision,
} from "./config-registry-common.js"
import {
	enforceRegistryRootFreshness,
	parseRegistryRootEnvelope,
	registryRootFileDigests,
	sortedRegistryRootFiles,
	verifyRegistryRootSignatures,
} from "./config-registry-root.js"
import {
	REGISTRY_ROOT_FILENAME,
	type ConfigRegistryOpenOptions,
	type CurrentPointer,
	type JsonObject,
	type RegistryManifest,
	type RegistryRootPayload,
	type RegistryRootVerificationOptions,
} from "./config-registry-types.js"

export class ConfigRegistryManager {
	readonly root: string
	readonly snapshotsDir: string
	readonly currentPath: string
	private readonly signedRootOptions: RegistryRootVerificationOptions | undefined

	private activeRevisionValue: string | null = null
	private activeManifestSha256Value: string | null = null
	private configs = new Map<string, LLMConfig>()
	private lastReloadErrorValue: Error | null = null
	private lastSuccessfulReloadAtValue: Date | null = null
	private lastReloadFailureAtValue: Date | null = null
	private refreshPromise: Promise<boolean> | null = null

	private constructor(root: string, options?: ConfigRegistryOpenOptions) {
		this.root = path.resolve(root)
		this.snapshotsDir = path.join(this.root, "snapshots")
		this.currentPath = path.join(this.root, "current.json")
		this.signedRootOptions = options?.signedRoot
	}

	static async open(root: string, options?: ConfigRegistryOpenOptions): Promise<ConfigRegistryManager> {
		const manager = new ConfigRegistryManager(root, options)
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
				if (this.shouldFailClosedOnRefreshError()) {
					throw error
				}
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
					if (this.shouldFailClosedOnRefreshError()) {
						throw this.lastReloadErrorValue ?? new InvalidConfigError("Config Registry refresh failed")
					}
					return
				}
			}
		}

		private shouldFailClosedOnRefreshError(): boolean {
			return this.signedRootOptions !== undefined
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
		const manifest = parseManifest(
			await this.readVerifiedJsonObject(manifestPath, pointer.manifestSha256, revision),
			manifestPath,
			revision,
		)
		await this.verifySignedRegistryRootIfNeeded(pointer, manifest, snapshotPath)
		const configs = new Map<string, LLMConfig>()

		for (const [presetId, entry] of Object.entries(manifest.presets)) {
			const authoringPath = await this.resolveSnapshotArtifact(snapshotPath, entry.authoring_path, presetId)
			await this.readVerifiedArtifactBytes(authoringPath, entry.authoring_sha256, presetId)
			const resolvedPath = await this.resolveSnapshotArtifact(snapshotPath, entry.resolved_path, presetId)
			const resolved = fromCanonicalResolvedConfig(
				await this.readVerifiedJsonObject(resolvedPath, entry.resolved_sha256, presetId),
				resolvedPath,
			)
			configs.set(presetId, resolved)
		}

		return configs
	}

	private async verifySignedRegistryRootIfNeeded(
		pointer: CurrentPointer,
		manifest: RegistryManifest,
		snapshotPath: string,
	): Promise<void> {
		if (this.signedRootOptions === undefined) {
			return
		}
		const rootPath = path.join(snapshotPath, REGISTRY_ROOT_FILENAME)
		const rootDigest = await sha256File(rootPath)
		const envelope = parseRegistryRootEnvelope(await readJsonObject(rootPath), rootPath)
		await verifyRegistryRootSignatures(envelope, this.signedRootOptions)
		await enforceRegistryRootFreshness(envelope, this.signedRootOptions, rootDigest)
		await this.verifyRegistryRootPayload(envelope.payload, pointer, manifest, snapshotPath)
	}

	private async verifyRegistryRootPayload(
		payload: RegistryRootPayload,
		pointer: CurrentPointer,
		manifest: RegistryManifest,
		snapshotPath: string,
	): Promise<void> {
		this.verifyRegistryRootBindings(payload, pointer, manifest)

		const expectedFiles = registryRootFileDigests(manifest)
		const actualFiles = sortedRegistryRootFiles(payload.files)
		if (canonicalJsonString(actualFiles) !== canonicalJsonString(expectedFiles)) {
			throw new SecurityError("Registry root file digest set does not match the selected manifest")
		}

		for (const file of actualFiles) {
			const relativePath = snapshotRelativePath(pointer.revision, file.path)
			const artifactPath = path.join(snapshotPath, relativePath)
			await verifyPathContainmentAsync(artifactPath, snapshotPath)
			const actualSha = await sha256File(artifactPath)
			if (actualSha !== file.sha256) {
				throw new SecurityError(`Registry root file digest mismatch: ${file.path}`)
			}
		}
	}

	private verifyRegistryRootBindings(
		payload: RegistryRootPayload,
		pointer: CurrentPointer,
		manifest: RegistryManifest,
	): void {
		if (payload.revision !== pointer.revision || payload.revision !== manifest.revision) {
			throw new SecurityError("Registry root revision does not match the active current pointer")
		}
		if (payload.current.revision !== pointer.revision || payload.current.manifest_sha256 !== pointer.manifestSha256) {
			throw new SecurityError("Registry root current binding does not match current.json")
		}
		if (payload.current.sha256 !== currentPointerSha256(pointer)) {
			throw new SecurityError("Registry root current binding digest mismatch")
		}
		if (payload.manifest.path !== snapshotRegistryPath(pointer.revision, "manifest.json")) {
			throw new SecurityError("Registry root manifest path does not match the active snapshot")
		}
		if (payload.manifest.sha256 !== pointer.manifestSha256) {
			throw new SecurityError("Registry root manifest digest does not match current.json")
		}
	}

	private async resolveSnapshotArtifact(snapshotPath: string, relativePath: string, presetId: string): Promise<string> {
		if (!relativePath) {
			throw new InvalidConfigError(`Config Registry manifest entry is missing artifact path: ${presetId}`)
		}
		const artifactPath = path.join(snapshotPath, relativePath)
		await verifyPathContainmentAsync(artifactPath, snapshotPath)
		return artifactPath
	}

	private async readVerifiedArtifactBytes(artifactPath: string, expectedSha: string, presetId: string): Promise<Uint8Array> {
		if (!expectedSha) {
			throw new InvalidConfigError(`Config Registry manifest entry is missing checksum: ${presetId}`)
		}
		const content = await readFileBytes(artifactPath)
		const actualSha = sha256Bytes(content)
		if (actualSha !== expectedSha) {
			throw new InvalidConfigError(`Checksum mismatch for Config Registry artifact ${artifactPath}`)
		}
		return content
	}

	private async readVerifiedJsonObject(artifactPath: string, expectedSha: string, presetId: string): Promise<JsonObject> {
		const content = await this.readVerifiedArtifactBytes(artifactPath, expectedSha, presetId)
		return parseJsonObjectBytes(content, artifactPath)
	}

	private recordReloadError(error: unknown): void {
		this.lastReloadErrorValue = error instanceof Error ? error : new Error(String(error))
		this.lastReloadFailureAtValue = new Date()
	}
}
