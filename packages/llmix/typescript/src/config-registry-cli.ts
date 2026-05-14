import { cp, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"

import {
	ConfigRegistryManager,
	ConfigRegistryPublisher,
	loadLlmixTrustManifest,
	registryRootOptionsFromTrustManifest,
} from "./index.js"
import { InvalidConfigError } from "./types.js"
import { isJsonObject, readJsonObject, sha256Bytes } from "./config-registry-common.js"
import {
	CliError,
	assertOutsideDir,
	assertSameDir,
	buildRegistryRootSigner,
	buildVerificationHooks,
	loadTrustPolicyFile,
	optionValue,
	parseCliArgs,
	readJsonFile,
	rejectUnknownOptions,
	repeatedOption,
	requiredOption,
	resolvePath,
	sha256FilePrefixed,
	stripSha256Prefix,
	type CliIo,
	type ParsedCliArgs,
} from "./config-registry-cli-support.js"
import type { JsonObject, JsonValue, RegistryRootVerificationOptions } from "./config-registry-types.js"

const HELP = `LLMix registry CLI

Usage:
  llmix publish-registry --release-plan <file> --revision <id> --policy <file> --root-did <did> --root-key-id <id> --root-key-file <pem> [--root config/llm] [--did-document <file>] [--json] [--no-activate]
  llmix check-registry --trust <file> --preset <module/preset> [--root config/llm] [--did-document <file>] [--tamper-proof] [--json]

Commands:
  publish-registry  Publish config/llm/source into signed config/llm/compiled output.
  check-registry    Open config/llm through the LLMIx runtime with an external trust anchor.
`

const PUBLISH_OPTIONS = [
	"--root",
	"--release-plan",
	"--revision",
	"--policy",
	"--did-document",
	"--rekor-entry",
	"--rekor-url",
	"--root-did",
	"--root-key-id",
	"--root-key-file",
]
const CHECK_OPTIONS = ["--root", "--trust", "--preset", "--did-document", "--rekor-entry", "--rekor-url"]
const GLOBAL_FLAGS = ["--help", "-h", "--json"]

export async function runCli(argv: readonly string[] = process.argv.slice(2), io: CliIo = defaultIo()): Promise<number> {
	const args = parseCliArgs(argv)
	const json = args.flags.has("--json")
	try {
		if (args.command === null || args.flags.has("--help") || args.flags.has("-h")) {
			io.stdout(HELP.trimEnd())
			return 0
		}
		if (args.command === "publish-registry") {
			const result = await publishRegistry(args)
			writeResult(io, json, result, publishText(result))
			return 0
		}
		if (args.command === "check-registry") {
			const result = await checkRegistry(args)
			writeResult(io, json, result, checkText(result))
			return 0
		}
		throw new CliError(`Unknown command: ${args.command}`, 2)
	} catch (error) {
		const exitCode = error instanceof CliError ? error.exitCode : 1
		const message = error instanceof Error ? error.message : String(error)
		if (json) {
			io.stderr(JSON.stringify({ ok: false, error: message }, null, 2))
		} else {
			io.stderr(message)
		}
		return exitCode
	}
}

async function publishRegistry(args: ParsedCliArgs): Promise<JsonObject> {
	rejectUnknownOptions(args, PUBLISH_OPTIONS, [...GLOBAL_FLAGS, "--no-activate"])
	const root = resolvePath(optionValue(args, "--root") ?? "config/llm")
	const releasePlanPath = resolvePath(requiredOption(args, "--release-plan"))
	const revision = requiredOption(args, "--revision")
	const policyPath = resolvePath(requiredOption(args, "--policy"))
	const rootDid = requiredOption(args, "--root-did")
	const rootKeyId = requiredOption(args, "--root-key-id")
	const rootKeyFile = resolvePath(requiredOption(args, "--root-key-file"))
	assertOutsideDir(releasePlanPath, root, "release plan")

	const releasePlan = await loadReleasePlan(releasePlanPath)
	assertSameDir(releasePlan.registryDir, root, "release plan registryDir")
	await verifyReleasePlanSources(root, releasePlan)

	const policy = await loadTrustPolicyFile(policyPath)
	const hooks = await buildVerificationHooks({
		didDocumentPaths: repeatedOption(args, "--did-document").map(resolvePath),
		rekorEntryPaths: repeatedOption(args, "--rekor-entry").map(resolvePath),
		rekorUrl: optionValue(args, "--rekor-url"),
		policy,
	})
	const signer = await buildRegistryRootSigner({ rootDid, rootKeyId, rootKeyFile })
	const published = await new ConfigRegistryPublisher(root).publish({
		revision,
		activate: !args.flags.has("--no-activate"),
		trustedRuntime: true,
		trustPolicy: policy,
		...hooks,
		registryRoot: { signer },
	})
	if (published.registryRootPath === undefined || published.registryRootSha256 === undefined) {
		throw new InvalidConfigError("Registry publisher did not produce a signed registry root")
	}
	return {
		ok: true,
		command: "publish-registry",
		root,
		releasePlan: releasePlanPath,
		revision: published.revision,
		activated: published.activated,
		current: path.join(root, "current.json"),
		compiledPath: published.compiledPath,
		manifestPath: published.manifestPath,
		manifestSha256: published.manifestSha256,
		registryRootPath: published.registryRootPath,
		registryRootSha256: published.registryRootSha256,
		presetIds: published.presetIds,
	}
}

async function checkRegistry(args: ParsedCliArgs): Promise<JsonObject> {
	rejectUnknownOptions(args, CHECK_OPTIONS, [...GLOBAL_FLAGS, "--tamper-proof"])
	const root = resolvePath(optionValue(args, "--root") ?? "config/llm")
	const trustPath = resolvePath(requiredOption(args, "--trust"))
	const preset = requiredOption(args, "--preset")
	assertOutsideDir(trustPath, root, "trust anchor")

	const [moduleName, presetName] = parsePresetId(preset)
	const manifest = await loadLlmixTrustManifest(trustPath)
	const hooks = await buildVerificationHooks({
		didDocumentPaths: repeatedOption(args, "--did-document").map(resolvePath),
		rekorEntryPaths: repeatedOption(args, "--rekor-entry").map(resolvePath),
		rekorUrl: optionValue(args, "--rekor-url"),
		policy: manifest.registryRootTrustPolicy,
	})
	const signedRoot = registryRootOptionsFromTrustManifest(manifest, hooks)
	const manager = await ConfigRegistryManager.open(root, { signedRoot })
	const config = await manager.getPreset(moduleName, presetName)
	const tamperProof = args.flags.has("--tamper-proof") ? await proveTamperRejection(root, signedRoot) : null

	return {
		ok: true,
		command: "check-registry",
		root,
		trust: trustPath,
		activeRevision: manager.activeRevision,
		preset,
		provider: config.provider,
		model: config.model,
		tamperProof,
	}
}

interface ReleasePlanSource {
	module: string
	preset: string
	sourcePath: string
	rawSourceDigest: string
	expectedRegistryEntryIdentity: string
	sourceSetEntry: JsonObject
}

interface ReleasePlan {
	kind: "llmix-release-plan"
	registryDir: string
	sourceSetDigest: string
	sources: ReleasePlanSource[]
}

async function loadReleasePlan(filePath: string): Promise<ReleasePlan> {
	const value = await readJsonFile(filePath)
	if (value["kind"] !== "llmix-release-plan") {
		throw new InvalidConfigError("release plan kind must be llmix-release-plan")
	}
	const registryDir = requireString(value, "registryDir", filePath)
	const sourceSetDigest = requireDigest(value, "sourceSetDigest", filePath)
	const rawSources = value["sources"]
	if (!Array.isArray(rawSources)) {
		throw new InvalidConfigError("release plan sources must be an array")
	}
	const sources = rawSources.map((source, index) => parseReleasePlanSource(source, `${filePath}.sources[${index}]`))
	return { kind: "llmix-release-plan", registryDir, sourceSetDigest, sources }
}

function parseReleasePlanSource(value: unknown, label: string): ReleasePlanSource {
	if (!isJsonObject(value)) {
		throw new InvalidConfigError(`${label} must be a JSON object`)
	}
	const moduleName = requireString(value, "module", label)
	const presetName = requireString(value, "preset", label)
	const sourcePath = requireString(value, "sourcePath", label)
	const rawSourceDigest = requireDigest(value, "rawSourceDigest", label)
	const expectedRegistryEntryIdentity = requireString(value, "expectedRegistryEntryIdentity", label)
	const expectedSourcePath = path.posix.join(moduleName, `${presetName}.mda`)
	if (sourcePath !== expectedSourcePath) {
		throw new InvalidConfigError(`${label}.sourcePath must be ${expectedSourcePath}`)
	}
	if (expectedRegistryEntryIdentity !== `${moduleName}/${presetName}`) {
		throw new InvalidConfigError(`${label}.expectedRegistryEntryIdentity does not match module/preset`)
	}
	return { module: moduleName, preset: presetName, sourcePath, rawSourceDigest, expectedRegistryEntryIdentity, sourceSetEntry: value }
}

async function verifyReleasePlanSources(root: string, releasePlan: ReleasePlan): Promise<void> {
	const actual = await scanRegistrySources(path.join(root, "source"))
	const expected = new Map<string, ReleasePlanSource>()
	for (const source of releasePlan.sources) {
		const key = `${source.module}/${source.preset}`
		if (expected.has(key)) {
			throw new InvalidConfigError(`release plan contains duplicate source: ${key}`)
		}
		expected.set(key, source)
	}
	const actualSourceSetDigest = releasePlanSourceSetDigest(releasePlan.sources)
	if (actualSourceSetDigest !== releasePlan.sourceSetDigest) {
		throw new InvalidConfigError("release plan sourceSetDigest does not match sources")
	}
	for (const key of expected.keys()) {
		if (!actual.has(key)) {
			throw new InvalidConfigError(`release plan source is missing from config/llm/source: ${key}`)
		}
	}
	for (const key of actual.keys()) {
		if (!expected.has(key)) {
			throw new InvalidConfigError(`config/llm/source contains a source not in the release plan: ${key}`)
		}
	}
	for (const [key, source] of expected) {
		const filePath = actual.get(key)
		if (filePath === undefined) {
			throw new InvalidConfigError(`release plan source is missing from config/llm/source: ${key}`)
		}
		const actualDigest = await sha256FilePrefixed(filePath)
		if (actualDigest !== source.rawSourceDigest) {
			throw new InvalidConfigError(`release plan rawSourceDigest does not match config/llm/source for ${key}`)
		}
	}
}

function releasePlanSourceSetDigest(sources: readonly ReleasePlanSource[]): string {
	return `sha256:${sha256Bytes(mdaCanonicalJson({ sources: sources.map((source) => source.sourceSetEntry) }))}`
}

function mdaCanonicalJson(value: JsonObject | JsonValue): string {
	if (value === null) {
		return "null"
	}
	if (typeof value === "string") {
		return JSON.stringify(value)
	}
	if (typeof value === "number") {
		if (!Number.isFinite(value)) {
			throw new InvalidConfigError("release plan sourceSetDigest contains a non-finite number")
		}
		return JSON.stringify(value)
	}
	if (typeof value === "boolean") {
		return value ? "true" : "false"
	}
	if (Array.isArray(value)) {
		return `[${value.map((item) => mdaCanonicalJson(item)).join(",")}]`
	}
	if (isJsonObject(value)) {
		return `{${Object.keys(value)
			.sort()
			.filter((key) => value[key] !== undefined)
			.map((key) => {
				const item = value[key]
				return `${JSON.stringify(key)}:${mdaCanonicalJson(item ?? null)}`
			})
			.join(",")}}`
	}
	return "null"
}

async function scanRegistrySources(sourceDir: string): Promise<Map<string, string>> {
	const result = new Map<string, string>()
	for (const moduleEntry of await readdir(sourceDir, { withFileTypes: true })) {
		if (!moduleEntry.isDirectory()) {
			continue
		}
		const moduleName = moduleEntry.name
		const moduleDir = path.join(sourceDir, moduleName)
		for (const presetEntry of await readdir(moduleDir, { withFileTypes: true })) {
			if (!presetEntry.isFile() || !presetEntry.name.endsWith(".mda")) {
				continue
			}
			const presetName = presetEntry.name.slice(0, -".mda".length)
			result.set(`${moduleName}/${presetName}`, path.join(moduleDir, presetEntry.name))
		}
	}
	return result
}

async function proveTamperRejection(root: string, signedRoot: RegistryRootVerificationOptions): Promise<JsonObject> {
	const tempParent = await mkdtemp(path.join(tmpdir(), "llmix-registry-check-"))
	const tempRoot = path.join(tempParent, "config", "llm")
	try {
		await cp(root, tempRoot, { recursive: true })
		const current = await readJsonObject(path.join(tempRoot, "current.json"))
		const revision = requireString(current, "revision", "current.json")
		const manifestPath = path.join(tempRoot, "compiled", revision, "manifest.json")
		const manifest = await readJsonObject(manifestPath)
		const presets = manifest["presets"]
		if (!isJsonObject(presets)) {
			throw new InvalidConfigError("compiled manifest presets must be a JSON object")
		}
		const firstEntry = Object.values(presets).find(isJsonObject)
		if (firstEntry === undefined) {
			throw new InvalidConfigError("compiled manifest has no presets to tamper")
		}
		const resolvedPath = requireString(firstEntry, "resolved_path", "compiled manifest preset")
		const tamperedPath = path.join(tempRoot, "compiled", revision, resolvedPath)
		await writeFile(tamperedPath, Buffer.concat([await readFile(tamperedPath), Buffer.from("\n")]))
		try {
			await ConfigRegistryManager.open(tempRoot, { signedRoot })
		} catch (error) {
			return {
				ok: true,
				rejected: true,
				message: error instanceof Error ? error.message : String(error),
			}
		}
		throw new InvalidConfigError("tamper proof failed: modified registry content was accepted")
	} finally {
		await rm(tempParent, { recursive: true, force: true })
	}
}

function parsePresetId(value: string): [string, string] {
	const parts = value.split("/")
	const moduleName = parts[0]
	const presetName = parts[1]
	if (parts.length !== 2 || moduleName === undefined || presetName === undefined || moduleName === "" || presetName === "") {
		throw new CliError("--preset must use <module>/<preset>", 2)
	}
	return [moduleName, presetName]
}

function requireString(value: JsonObject, field: string, label: string): string {
	const item = value[field]
	if (typeof item !== "string" || item.length === 0) {
		throw new InvalidConfigError(`${label}.${field} must be a non-empty string`)
	}
	return item
}

function requireDigest(value: JsonObject, field: string, label: string): string {
	const digest = requireString(value, field, label)
	stripSha256Prefix(digest, `${label}.${field}`)
	return digest
}

function writeResult(io: CliIo, json: boolean, result: JsonObject, text: string): void {
	io.stdout(json ? JSON.stringify(result, null, 2) : text)
}

function publishText(result: JsonObject): string {
	return [
		`Published LLMix registry revision ${String(result["revision"])}`,
		`current.json: ${result["activated"] === true ? "updated" : "not updated"}`,
		`compiled: ${String(result["compiledPath"])}`,
		`registry root: ${String(result["registryRootPath"])}`,
	].join("\n")
}

function checkText(result: JsonObject): string {
	const lines = [
		`Checked LLMix registry revision ${String(result["activeRevision"])}`,
		`preset: ${String(result["preset"])}`,
		`model: ${String(result["provider"])}/${String(result["model"])}`,
	]
	const tamperProof = result["tamperProof"]
	if (isJsonObject(tamperProof) && tamperProof["rejected"] === true) {
		lines.push("tamper proof: rejected modified registry content")
	}
	return lines.join("\n")
}

function defaultIo(): CliIo {
	return {
		stdout(message) {
			process.stdout.write(`${message}\n`)
		},
		stderr(message) {
			process.stderr.write(`${message}\n`)
		},
	}
}
