import { Buffer } from "node:buffer"
import {
	constants as cryptoConstants,
	createPrivateKey,
	createPublicKey,
	sign as cryptoSign,
	verify as cryptoVerify,
	type KeyObject,
	type webcrypto,
} from "node:crypto"
import { readFile } from "node:fs/promises"
import path from "node:path"

import {
	constructDssePae,
	officialSigstoreVerifier,
	validateTrustPolicy,
	type DidWebVerifier,
	type RekorClient,
	type RekorEntry,
	type SigstoreVerifier,
	type TrustPolicy,
} from "@snoai/mda-config"

import { InvalidConfigError, SecurityError } from "./types.js"
import { isJsonObject, normalizeSha256Digest, parseJsonObjectBytes, sha256Bytes } from "./config-registry-common.js"
import {
	REGISTRY_ROOT_PAYLOAD_TYPE,
	type JsonObject,
	type RegistryRootSignature,
	type RegistryRootSigner,
} from "./config-registry-types.js"

type JsonWebKey = webcrypto.JsonWebKey

export interface CliIo {
	stdout(message: string): void
	stderr(message: string): void
}

export interface ParsedCliArgs {
	command: string | null
	options: Map<string, string[]>
	flags: Set<string>
	positionals: string[]
}

export class CliError extends Error {
	readonly exitCode: number

	constructor(message: string, exitCode = 1) {
		super(message)
		this.name = "CliError"
		this.exitCode = exitCode
	}
}

export function parseCliArgs(argv: readonly string[]): ParsedCliArgs {
	const command = argv[0]?.startsWith("-") === true ? null : (argv[0] ?? null)
	const options = new Map<string, string[]>()
	const flags = new Set<string>()
	const positionals: string[] = []

	for (let index = command === null ? 0 : 1; index < argv.length; index++) {
		const token = argv[index]
		if (token === undefined) {
			continue
		}
		if (!token.startsWith("--")) {
			positionals.push(token)
			continue
		}
		const equals = token.indexOf("=")
		if (equals > 0) {
			addOption(options, token.slice(0, equals), token.slice(equals + 1))
			continue
		}
		const next = argv[index + 1]
		if (next !== undefined && !next.startsWith("--")) {
			addOption(options, token, next)
			index++
			continue
		}
		flags.add(token)
	}

	return { command, options, flags, positionals }
}

export function optionValue(args: ParsedCliArgs, name: string): string | null {
	const values = args.options.get(name)
	if (values === undefined || values.length === 0) {
		return null
	}
	if (values.length > 1) {
		throw new CliError(`${name} must be provided only once`, 2)
	}
	const value = values[0]
	if (value === undefined || value.length === 0) {
		throw new CliError(`${name} requires a value`, 2)
	}
	return value
}

export function requiredOption(args: ParsedCliArgs, name: string): string {
	const value = optionValue(args, name)
	if (value === null) {
		throw new CliError(`${name} is required`, 2)
	}
	return value
}

export function repeatedOption(args: ParsedCliArgs, name: string): string[] {
	return args.options.get(name) ?? []
}

export function rejectUnknownOptions(args: ParsedCliArgs, allowedOptions: readonly string[], allowedFlags: readonly string[]): void {
	const allowedOptionSet = new Set(allowedOptions)
	const allowedFlagSet = new Set(allowedFlags)
	for (const name of args.options.keys()) {
		if (!allowedOptionSet.has(name)) {
			throw new CliError(`Unknown option: ${name}`, 2)
		}
	}
	for (const name of args.flags) {
		if (!allowedFlagSet.has(name)) {
			throw new CliError(`Unknown flag: ${name}`, 2)
		}
	}
	if (args.positionals.length > 0) {
		throw new CliError(`Unexpected argument: ${args.positionals[0]}`, 2)
	}
}

export function resolvePath(input: string): string {
	return path.resolve(process.cwd(), input)
}

export function assertOutsideDir(candidate: string, dir: string, label: string): void {
	const resolvedCandidate = path.resolve(candidate)
	const resolvedDir = path.resolve(dir)
	const relative = path.relative(resolvedDir, resolvedCandidate)
	if (relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
		throw new SecurityError(`${label} must be outside the registry root: ${resolvedCandidate}`)
	}
}

export function assertSameDir(left: string, right: string, label: string): void {
	if (path.resolve(left) !== path.resolve(right)) {
		throw new InvalidConfigError(`${label} does not match registry root`)
	}
}

export async function readJsonFile(filePath: string): Promise<JsonObject> {
	return parseJsonObjectBytes(await readFile(filePath), filePath)
}

export async function sha256FilePrefixed(filePath: string): Promise<string> {
	return `sha256:${sha256Bytes(await readFile(filePath))}`
}

export function loadTrustPolicy(value: unknown, label: string): TrustPolicy {
	try {
		return validateTrustPolicy(value)
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error)
		throw new InvalidConfigError(`Invalid trust policy in ${label}: ${message}`)
	}
}

export async function loadTrustPolicyFile(filePath: string): Promise<TrustPolicy> {
	return loadTrustPolicy(await readJsonFile(filePath), filePath)
}

export async function buildVerificationHooks(options: {
	didDocumentPaths: readonly string[]
	rekorEntryPaths: readonly string[]
	rekorUrl: string | null
	policy: TrustPolicy
}): Promise<{ didWebVerifier?: DidWebVerifier; rekorClient?: RekorClient; sigstoreVerifier?: SigstoreVerifier }> {
	const result: { didWebVerifier?: DidWebVerifier; rekorClient?: RekorClient; sigstoreVerifier?: SigstoreVerifier } = {}
	const hasDidWeb = options.policy.trustedSigners.some((signer) => signer.type === "did-web")
	if (hasDidWeb && options.didDocumentPaths.length === 0) {
		throw new CliError("did:web policy requires --did-document", 2)
	}
	if (options.didDocumentPaths.length > 0) {
		result.didWebVerifier = await didWebVerifierFromDocuments(options.didDocumentPaths)
	}
	const hasSigstore = options.policy.trustedSigners.some((signer) => signer.type === "sigstore-oidc")
	if (hasSigstore) {
		const rekorUrl = options.rekorUrl ?? options.policy.rekor?.url
		if (rekorUrl === undefined) {
			throw new CliError("Sigstore policy requires --rekor-url or policy.rekor.url", 2)
		}
		result.rekorClient = await rekorClientFromOptions(rekorUrl, options.rekorEntryPaths)
		result.sigstoreVerifier = officialSigstoreVerifier()
	}
	return result
}

export async function buildRegistryRootSigner(options: {
	rootDid: string
	rootKeyId: string
	rootKeyFile: string
}): Promise<RegistryRootSigner> {
	const key = createPrivateKey(await readFile(options.rootKeyFile))
	const algorithm = algorithmForKey(key)
	const signer = signerFromRootDid(options.rootDid)
	return (input) => {
		const paeBytes = constructDssePae(input.payloadType, Buffer.from(input.canonicalPayload, "utf8"))
		const signature: RegistryRootSignature = {
			signer,
			"key-id": options.rootKeyId,
			algorithm,
			"payload-type": REGISTRY_ROOT_PAYLOAD_TYPE,
			"payload-digest": input.integrity.digest,
			signature: signBytes(key, algorithm, paeBytes).toString("base64"),
		}
		return signature
	}
}

function addOption(options: Map<string, string[]>, name: string, value: string): void {
	const values = options.get(name) ?? []
	values.push(value)
	options.set(name, values)
}

async function didWebVerifierFromDocuments(paths: readonly string[]): Promise<DidWebVerifier> {
	const documents = new Map<string, JsonObject>()
	for (const documentPath of paths) {
		const document = await readJsonFile(documentPath)
		const id = document["id"]
		if (typeof id !== "string" || !id.startsWith("did:web:")) {
			throw new InvalidConfigError(`DID document must have a did:web id: ${documentPath}`)
		}
		documents.set(id.slice("did:web:".length), document)
	}

	return {
		async verify(input) {
			const document = documents.get(input.domain)
			if (document === undefined) {
				return false
			}
			const method = findVerificationMethod(document, input.keyId)
			if (method === null) {
				return false
			}
			const publicKey = publicKeyFromVerificationMethod(method)
			return verifyBytes(input.algorithm, publicKey, input.paeBytes, Buffer.from(input.signature, "base64"))
		},
	}
}

function findVerificationMethod(document: JsonObject, keyId: string): JsonObject | null {
	const methods = document["verificationMethod"]
	if (!Array.isArray(methods)) {
		return null
	}
	for (const method of methods) {
		if (!isJsonObject(method)) {
			continue
		}
		if (method["id"] === keyId) {
			return method
		}
	}
	return null
}

function publicKeyFromVerificationMethod(method: JsonObject): KeyObject {
	const jwk = method["publicKeyJwk"]
	if (isJsonObject(jwk)) {
		return createPublicKey({ key: jwk as JsonWebKey, format: "jwk" })
	}
	const pem = method["publicKeyPem"] ?? method["publicKeyMultibase"]
	if (typeof pem === "string" && pem.includes("BEGIN PUBLIC KEY")) {
		return createPublicKey(pem)
	}
	throw new InvalidConfigError("DID verification method must contain publicKeyJwk or publicKeyPem")
}

async function rekorClientFromOptions(rekorUrl: string, entryPaths: readonly string[]): Promise<RekorClient> {
	const entries = await Promise.all(
		entryPaths.map(async (entryPath) => (await readJsonFile(entryPath)) as unknown as RekorEntry),
	)
	return {
		url: rekorUrl,
		rekorUrl,
		async fetchEntry(requestedUrl, logId, logIndex) {
			if (requestedUrl !== rekorUrl) {
				throw new SecurityError("Requested Rekor URL does not match the configured policy")
			}
			const fixture = entries.find((entry) => entry.logId === logId && entry.logIndex === logIndex)
			if (fixture !== undefined) {
				return fixture
			}
			if (entries.length > 0) {
				return null
			}
			const url = `${rekorUrl.replace(/\/+$/, "")}/api/v1/log/entries?logIndex=${encodeURIComponent(String(logIndex))}`
			const response = await fetch(url)
			if (!response.ok) {
				return null
			}
			const value = (await response.json()) as unknown
			if (isJsonObject(value)) {
				const first = Object.values(value)[0]
				if (isJsonObject(first)) {
					return first as unknown as RekorEntry
				}
				return value as unknown as RekorEntry
			}
			return null
		},
	}
}

function signerFromRootDid(rootDid: string): string {
	if (rootDid.startsWith("did:web:")) {
		return `did-web:${rootDid.slice("did:web:".length)}`
	}
	return rootDid
}

function algorithmForKey(key: KeyObject): RegistryRootSignature["algorithm"] {
	if (key.asymmetricKeyType === "ed25519") {
		return "ed25519"
	}
	if (key.asymmetricKeyType === "ec") {
		return "ecdsa-p256"
	}
	if (key.asymmetricKeyType === "rsa" || key.asymmetricKeyType === "rsa-pss") {
		return "rsa-pss-sha256"
	}
	throw new InvalidConfigError(`Unsupported registry-root private key type: ${String(key.asymmetricKeyType)}`)
}

function signBytes(key: KeyObject, algorithm: RegistryRootSignature["algorithm"], bytes: Uint8Array): Buffer {
	if (algorithm === "ed25519") {
		return cryptoSign(null, bytes, key)
	}
	if (algorithm === "rsa-pss-sha256") {
		return cryptoSign("sha256", bytes, {
			key,
			padding: cryptoConstants.RSA_PKCS1_PSS_PADDING,
			saltLength: cryptoConstants.RSA_PSS_SALTLEN_DIGEST,
		})
	}
	return cryptoSign("sha256", bytes, key)
}

function verifyBytes(
	algorithm: RegistryRootSignature["algorithm"],
	key: KeyObject,
	bytes: Uint8Array,
	signature: Buffer,
): boolean {
	if (algorithm === "ed25519") {
		return cryptoVerify(null, bytes, key, signature)
	}
	if (algorithm === "rsa-pss-sha256") {
		return cryptoVerify(
			"sha256",
			bytes,
			{
				key,
				padding: cryptoConstants.RSA_PKCS1_PSS_PADDING,
				saltLength: cryptoConstants.RSA_PSS_SALTLEN_DIGEST,
			},
			signature,
		)
	}
	return cryptoVerify("sha256", bytes, key, signature)
}

export function stripSha256Prefix(digest: string, label: string): string {
	return normalizeSha256Digest(digest, label)
}
