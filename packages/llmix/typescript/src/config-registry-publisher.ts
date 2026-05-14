import { createHash, randomUUID } from "node:crypto"
import { mkdir, readdir, rename, rm } from "node:fs/promises"
import path from "node:path"

import { ConfigNotFoundError, InvalidConfigError } from "./types.js"
import { loadMdaConfig, type MdaConfigLoadOptions, validateModule, validatePreset, verifyPathContainmentAsync } from "./mda-loader.js"
import {
	atomicWriteJson,
	canonicalJsonString,
	fromCanonicalResolvedConfig,
	fsyncDir,
	isLegacyYamlPresetFilename,
	legacyYamlSourceError,
	manifestToJsonObject,
	mapReadError,
	parseMdaPresetFilename,
	parseManifest,
	readFileBytes,
	readJsonObject,
	sha256Bytes,
	sha256File,
	toCanonicalResolvedConfig,
	toMdaConfigLoadOptions,
	validateRevision,
	writeBytes,
	writeJson,
} from "./config-registry-common.js"
import { buildRegistryRootPayload, createRegistryRootEnvelope } from "./config-registry-root.js"
import {
	MANIFEST_SCHEMA_VERSION,
	REGISTRY_ROOT_FILENAME,
	type ConfigRegistryPublishOptions,
	type ManifestPresetEntry,
	type PresetSource,
	type PublishedRevision,
	type RegistryManifest,
} from "./config-registry-types.js"

export class ConfigRegistryPublisher {
	readonly root: string
	readonly sourceDir: string
	readonly compiledDir: string
	readonly stagingDir: string
	readonly currentPath: string

	constructor(root: string) {
		this.root = path.resolve(root)
		this.sourceDir = path.join(this.root, "source")
		this.compiledDir = path.join(this.root, "compiled")
		this.stagingDir = path.join(this.compiledDir, ".staging")
		this.currentPath = path.join(this.root, "current.json")
	}

	async publish(options?: ConfigRegistryPublishOptions): Promise<PublishedRevision> {
		const presets = await this.discoverPresets()
		if (presets.length === 0) {
			throw new ConfigNotFoundError(`No source presets found under ${this.sourceDir}`)
		}

		const publishedAt = new Date()
		const revisionId = options?.revision ?? (await this.buildRevisionId(presets, publishedAt))
		const activate = options?.activate ?? true
		validateRevision(revisionId)

		const compiledPath = path.join(this.compiledDir, revisionId)
		const stagePath = path.join(this.stagingDir, `${revisionId}.${process.pid}.${randomUUID()}.tmp`)
		const manifestPath = path.join(compiledPath, "manifest.json")

		try {
			const manifest = await this.buildStagedRevision(
				stagePath,
				presets,
				revisionId,
				publishedAt,
				toMdaConfigLoadOptions(options),
			)
			await this.verifyStagedRevision(stagePath, manifest)
			const manifestSha256 = await sha256File(path.join(stagePath, "manifest.json"))
			const registryRootSha256 = await this.writeRegistryRootIfRequested(stagePath, manifest, manifestSha256, options)
			await mkdir(this.compiledDir, { recursive: true })
			await mkdir(this.stagingDir, { recursive: true })
			const committed = await this.commitRevision(stagePath, compiledPath, manifest, manifestSha256, registryRootSha256)

			if (activate) {
				await atomicWriteJson(this.currentPath, { revision: revisionId, manifest_sha256: committed.manifestSha256 })
			}

			return {
				revision: revisionId,
				compiledPath,
				manifestPath,
				manifestSha256: committed.manifestSha256,
				...(committed.registryRootSha256 === undefined
					? {}
					: {
							registryRootPath: path.join(compiledPath, REGISTRY_ROOT_FILENAME),
							registryRootSha256: committed.registryRootSha256,
						}),
				activated: activate,
				presetIds: Object.keys(manifest.presets).sort(),
			}
		} catch (error) {
			await rm(stagePath, { recursive: true, force: true })
			throw error
		}
	}

	private async commitRevision(
		stagePath: string,
		compiledPath: string,
		manifest: RegistryManifest,
		manifestSha256: string,
		registryRootSha256: string | undefined,
	): Promise<{ manifestSha256: string; registryRootSha256?: string }> {
		try {
			await rename(stagePath, compiledPath)
			await fsyncDir(this.compiledDir)
			return registryRootSha256 === undefined ? { manifestSha256 } : { manifestSha256, registryRootSha256 }
		} catch (error) {
			if (!isExistingPathError(error)) {
				throw error
			}
		}

		const committed = await this.loadMatchingExistingRevision(compiledPath, manifest, registryRootSha256)
		await rm(stagePath, { recursive: true, force: true })
		return committed
	}

	private async loadMatchingExistingRevision(
		compiledPath: string,
		expectedManifest: RegistryManifest,
		expectedRegistryRootSha256: string | undefined,
	): Promise<{ manifestSha256: string; registryRootSha256?: string }> {
		const manifestPath = path.join(compiledPath, "manifest.json")
		const existingManifest = parseManifest(await readJsonObject(manifestPath), manifestPath, expectedManifest.revision)
		await this.verifyStagedRevision(compiledPath, existingManifest)
		if (!manifestPresetsMatch(existingManifest.presets, expectedManifest.presets)) {
			throw new InvalidConfigError(`Registry revision already exists with different contents: ${expectedManifest.revision}`)
		}
		const manifestSha256 = await sha256File(manifestPath)
		const registryRootSha256 = await optionalSha256File(path.join(compiledPath, REGISTRY_ROOT_FILENAME))
		if (expectedRegistryRootSha256 !== undefined && registryRootSha256 === undefined) {
			throw new InvalidConfigError(`Registry revision already exists without requested registry root: ${expectedManifest.revision}`)
		}
		if (expectedRegistryRootSha256 !== undefined && registryRootSha256 !== expectedRegistryRootSha256) {
			throw new InvalidConfigError(`Registry revision already exists with different registry root: ${expectedManifest.revision}`)
		}
		return registryRootSha256 === undefined ? { manifestSha256 } : { manifestSha256, registryRootSha256 }
	}

	private async writeRegistryRootIfRequested(
		stagePath: string,
		manifest: RegistryManifest,
		manifestSha256: string,
		options?: ConfigRegistryPublishOptions,
	): Promise<string | undefined> {
		if (options?.registryRoot === undefined) {
			return undefined
		}
		const payload = buildRegistryRootPayload(manifest, manifestSha256)
		const envelope = await createRegistryRootEnvelope(payload, options.registryRoot)
		const rootPath = path.join(stagePath, REGISTRY_ROOT_FILENAME)
		await writeJson(rootPath, envelope)
		return sha256File(rootPath)
	}

	private async discoverPresets(): Promise<PresetSource[]> {
		let moduleEntries: Array<{ name: string; isDirectory(): boolean }>
		try {
			moduleEntries = await readdir(this.sourceDir, { withFileTypes: true })
		} catch (error) {
			throw mapReadError(error, this.sourceDir)
		}

		const presets: PresetSource[] = []
		for (const moduleEntry of [...moduleEntries].sort((left, right) => left.name.localeCompare(right.name))) {
			if (!moduleEntry.isDirectory()) {
				continue
			}

			const moduleName = moduleEntry.name
			validateModule(moduleName)
			const modulePath = path.join(this.sourceDir, moduleName)

			const files = await readdir(modulePath, { withFileTypes: true })
			for (const fileEntry of [...files].sort((left, right) => left.name.localeCompare(right.name))) {
				if (!fileEntry.isFile()) {
					continue
				}

				const sourcePath = path.join(modulePath, fileEntry.name)
				if (isLegacyYamlPresetFilename(fileEntry.name)) {
					throw legacyYamlSourceError(sourcePath)
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
					sourcePath,
				})
			}
		}

		return presets
	}

	private async buildRevisionId(presets: PresetSource[], publishedAt: Date): Promise<string> {
		const hash = createHash("sha256")
		for (const preset of presets) {
			const relativePath = path.relative(this.sourceDir, preset.sourcePath)
			hash.update(relativePath)
			hash.update("\0")
			hash.update(await readFileBytes(preset.sourcePath))
			hash.update("\0")
		}

		const timestamp = publishedAt.toISOString().replace(/:/g, "-").replace(/\.\d{3}Z$/, "Z")
		return `${timestamp}_${hash.digest("hex").slice(0, 8)}`
	}

	private async buildStagedRevision(
		stagePath: string,
		presets: PresetSource[],
		revisionId: string,
		publishedAt: Date,
		loadOptions?: MdaConfigLoadOptions,
	): Promise<RegistryManifest> {
		const manifestPresets: Record<string, ManifestPresetEntry> = {}
		await mkdir(stagePath, { recursive: true })

		for (const preset of presets) {
			const sourceBytes = await readFileBytes(preset.sourcePath)
			const sourceRel = path.posix.join("source", preset.module, `${preset.preset}.mda`)
			const resolvedRel = path.posix.join("resolved", preset.module, `${preset.preset}.json`)
			const stagedSourcePath = path.join(stagePath, sourceRel)

			await writeBytes(stagedSourcePath, sourceBytes)

			const resolved = await loadMdaConfig(stagedSourcePath, loadOptions)
			const canonicalResolved = toCanonicalResolvedConfig(resolved)
			fromCanonicalResolvedConfig(canonicalResolved, stagedSourcePath)
			const resolvedBytes = canonicalJsonString(canonicalResolved)

			await writeBytes(path.join(stagePath, resolvedRel), resolvedBytes)

			manifestPresets[preset.presetId] = {
				source_path: sourceRel,
				source_sha256: sha256Bytes(sourceBytes),
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

	private async verifyStagedRevision(stagePath: string, manifest: RegistryManifest): Promise<void> {
		const storedManifest = await readJsonObject(path.join(stagePath, "manifest.json"))
		if (canonicalJsonString(storedManifest) !== canonicalJsonString(manifestToJsonObject(manifest))) {
			throw new InvalidConfigError("Staged registry manifest changed during verification")
		}

		for (const [presetId, entry] of Object.entries(manifest.presets)) {
			for (const [shaKey, pathKey] of [
				["source_sha256", "source_path"],
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

function isExistingPathError(error: unknown): boolean {
	return (
		error instanceof Error &&
		"code" in error &&
		((error as NodeJS.ErrnoException).code === "EEXIST" || (error as NodeJS.ErrnoException).code === "ENOTEMPTY")
	)
}

function manifestPresetsMatch(
	left: Record<string, ManifestPresetEntry>,
	right: Record<string, ManifestPresetEntry>,
): boolean {
	const leftIds = Object.keys(left).sort()
	const rightIds = Object.keys(right).sort()
	if (leftIds.length !== rightIds.length) {
		return false
	}
	for (const [index, presetId] of leftIds.entries()) {
		if (presetId !== rightIds[index]) {
			return false
		}
		const leftEntry = left[presetId]
		const rightEntry = right[presetId]
		if (leftEntry === undefined || rightEntry === undefined || !manifestPresetEntryMatches(leftEntry, rightEntry)) {
			return false
		}
	}
	return true
}

function manifestPresetEntryMatches(left: ManifestPresetEntry, right: ManifestPresetEntry): boolean {
	return (
		left.source_path === right.source_path &&
		left.source_sha256 === right.source_sha256 &&
		left.resolved_path === right.resolved_path &&
		left.resolved_sha256 === right.resolved_sha256
	)
}

async function optionalSha256File(filePath: string): Promise<string | undefined> {
	try {
		return await sha256File(filePath)
	} catch (error) {
		if (error instanceof ConfigNotFoundError) {
			return undefined
		}
		throw error
	}
}
