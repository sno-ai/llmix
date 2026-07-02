/**
 * MDA-based LLM Config Loader
 *
 * Loads MDA Source Mode presets through @snoai/mda-config, validates the
 * LLMix vendor namespace, and projects it into the runtime LLMConfig shape.
 */

import { realpathSync } from "node:fs";
import { realpath } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { loadMdaSource, type LoadMdaSourceOptions } from "@snoai/mda-config";
import { z } from "zod";
import {
  ANTHROPIC_MIN_BUDGET_TOKENS,
  type AnthropicCacheControl,
  type AnthropicProviderOptions,
  type AnthropicThinkingConfig,
  type CachingConfig,
  type CommonParams,
  ConfigAccessError,
  ConfigNotFoundError,
  type DeepSeekProviderOptions,
  type DeepSeekThinkingConfig,
  type GoogleProviderOptions,
  type GoogleSafetySetting,
  type GoogleThinkingConfig,
  InvalidConfigError,
  type LLMConfig,
  MAX_VERSION,
  MIN_VERSION,
  type OpenAIProviderOptions,
  type OpenRouterProviderOptions,
  type Provider,
  type ProviderOptions,
  SecurityError,
  type SnoGpuProviderOptions,
  type TimeoutConfig,
  VALID_MODULE_PATTERN,
  VALID_PRESET_PATTERN,
  VALID_SCOPE_PATTERN,
  VALID_USER_ID_PATTERN,
} from "./types.js";

// =============================================================================
// VALIDATION FUNCTIONS
// =============================================================================

/**
 * Validate module name against security rules
 *
 * @throws Error if module name is invalid
 */
export function validateModule(module: string): void {
  if (!module) {
    throw new Error("Module name cannot be empty");
  }

  if (module.length > 64) {
    throw new Error(`Module name too long: ${module.length} > 64`);
  }

  // Security: Prevent path traversal
  const dangerousChars = ["/", "\\", "..", "~", "$", "`"];
  if (dangerousChars.some((char) => module.includes(char))) {
    throw new SecurityError(`Invalid characters in module: ${module}`);
  }

  if (!VALID_MODULE_PATTERN.test(module)) {
    throw new Error(
      `Invalid module format: ${module}. ` +
        "Must be '_default' or start with lowercase letter and contain only lowercase letters, numbers, and underscores"
    );
  }
}

/**
 * Validate preset name against security rules
 *
 * @throws Error if preset name is invalid
 */
export function validatePreset(preset: string): void {
  if (!preset) {
    throw new Error("Preset name cannot be empty");
  }

  if (preset.length > 64) {
    throw new Error(`Preset name too long: ${preset.length} > 64`);
  }

  // Security: Prevent path traversal
  const dangerousChars = ["/", "\\", "..", "~", "$", "`"];
  if (dangerousChars.some((char) => preset.includes(char))) {
    throw new SecurityError(`Invalid characters in preset: ${preset}`);
  }

  if (!VALID_PRESET_PATTERN.test(preset)) {
    throw new Error(
      `Invalid preset format: ${preset}. ` +
        "Must be '_base*' or start with lowercase letter and contain only lowercase letters, numbers, and underscores"
    );
  }
}

/**
 * Validate scope name against security rules
 *
 * @throws Error if scope name is invalid
 */
export function validateScope(scope: string): void {
  if (!scope) {
    throw new Error("Scope name cannot be empty");
  }

  if (scope.length > 64) {
    throw new Error(`Scope name too long: ${scope.length} > 64`);
  }

  // Security: Prevent path traversal
  const dangerousChars = ["/", "\\", "..", "~", "$", "`"];
  if (dangerousChars.some((char) => scope.includes(char))) {
    throw new SecurityError(`Invalid characters in scope: ${scope}`);
  }

  if (!VALID_SCOPE_PATTERN.test(scope)) {
    throw new Error(
      `Invalid scope format: ${scope}. ` +
        "Must be '_default' or start with lowercase letter and contain only lowercase letters, numbers, underscores, and hyphens"
    );
  }
}

/**
 * Validate user ID against security rules
 *
 * @returns true if valid, false if invalid (allows graceful fallback)
 */
export function validateUserId(userId: string | undefined): boolean {
  if (!userId) {
    return false;
  }

  if (userId.length > 64) {
    return false;
  }

  // Security: Prevent path traversal
  const dangerousChars = ["/", "\\", "..", "~", "$", "`"];
  if (dangerousChars.some((char) => userId.includes(char))) {
    return false;
  }

  return VALID_USER_ID_PATTERN.test(userId);
}

/**
 * Validate version number
 *
 * @throws Error if version is out of valid range or not an integer
 */
export function validateVersion(version: number): void {
  if (!Number.isInteger(version)) {
    throw new TypeError(`Version must be an integer, got ${typeof version}`);
  }

  if (version < MIN_VERSION || version > MAX_VERSION) {
    throw new Error(`Version ${version} out of valid range [${MIN_VERSION}, ${MAX_VERSION}]`);
  }
}

// =============================================================================
// PATH BUILDING AND SECURITY
// =============================================================================

/**
 * Build config file path from components
 *
 * Path format: {configDir}/{module}/{preset}.mda
 * Note: scope is NOT part of the file path (used for cascade resolution)
 *
 * @param configDir - Base config directory
 * @param module - Module name (e.g., "hrkg", "_default")
 * @param preset - Preset name (e.g., "extraction", "_base")
 * @returns Resolved file path
 */
export function buildMdaConfigFilePath(
  configDir: string,
  module: string,
  preset: string
): string {
  const filename = `${preset}.mda`;
  return join(resolve(configDir), module, filename);
}

/**
 * Verify resolved path is within the allowed base directory
 *
 * Security: Prevents symlink-based path traversal attacks.
 *
 * @param resolvedPath - The resolved path to check
 * @param baseDir - The allowed base directory
 * @throws SecurityError if path escapes base directory
 */
export function verifyPathContainment(resolvedPath: string, baseDir: string): void {
  const normalizedBase = resolve(baseDir);
  const normalizedPath = resolve(resolvedPath);

  // Resolve symlinks to get actual filesystem path
  let realPath: string;
  try {
    realPath = realpathSync(normalizedPath);
  } catch {
    // File doesn't exist yet - use normalized path
    realPath = normalizedPath;
  }

  let realBase: string;
  try {
    realBase = realpathSync(normalizedBase);
  } catch {
    // Base dir doesn't exist - use normalized path
    realBase = normalizedBase;
  }

  // Cross-platform containment check using relative path
  const rel = relative(realBase, realPath);
  if (rel.startsWith("..") || isAbsolute(rel)) {
    throw new SecurityError(
      `Path traversal detected: ${resolvedPath} escapes base directory ${baseDir}`
    );
  }
}

/**
 * Verify resolved path is within the allowed base directory (async version)
 *
 * @param resolvedPath - The resolved path to check
 * @param baseDir - The allowed base directory
 * @throws SecurityError if path escapes base directory
 */
export async function verifyPathContainmentAsync(
  resolvedPath: string,
  baseDir: string
): Promise<void> {
  const normalizedBase = resolve(baseDir);
  const normalizedPath = resolve(resolvedPath);

  // Resolve symlinks to get actual filesystem path
  let realPath: string;
  try {
    realPath = await realpath(normalizedPath);
  } catch {
    // File doesn't exist yet - use normalized path
    realPath = normalizedPath;
  }

  let realBase: string;
  try {
    realBase = await realpath(normalizedBase);
  } catch {
    // Base dir doesn't exist - use normalized path
    realBase = normalizedBase;
  }

  // Cross-platform containment check using relative path
  const rel = relative(realBase, realPath);
  if (rel.startsWith("..") || isAbsolute(rel)) {
    throw new SecurityError(
      `Path traversal detected: ${resolvedPath} escapes base directory ${baseDir}`
    );
  }
}

// =============================================================================
// ZOD SCHEMAS - STRICT MODE (REJECT UNKNOWN KEYS)
// =============================================================================

/**
 * Common AI SDK v7 parameters schema
 */
export const CommonParamsSchema = z
  .object({
    maxOutputTokens: z.number().int().positive().optional(),
    temperature: z.number().min(0).max(2).optional(),
    topP: z.number().min(0).max(1).optional(),
    topK: z.number().int().positive().optional(),
    presencePenalty: z.number().optional(),
    frequencyPenalty: z.number().optional(),
    stopSequences: z.array(z.string()).optional(),
    seed: z.number().int().optional(),
    maxRetries: z.number().int().nonnegative().optional(),
    enableThinking: z.boolean().optional(),
    keepThinkingOutput: z.boolean().optional(),
  })
  .strict() satisfies z.ZodType<CommonParams>;

/**
 * OpenAI provider options schema
 */
export const OpenAIProviderOptionsSchema = z
  .object({
    reasoningEffort: z.enum(["minimal", "low", "medium", "high", "xhigh"]).optional(),
    parallelToolCalls: z.boolean().optional(),
    user: z.string().optional(),
    logprobs: z.union([z.boolean(), z.number().int().nonnegative()]).optional(),
    logitBias: z.record(z.string().regex(/^\d+$/), z.number()).optional(),
    structuredOutputs: z.boolean().optional(),
    strictJsonSchema: z.boolean().optional(),
    maxCompletionTokens: z.number().int().positive().optional(),
    store: z.boolean().optional(),
    metadata: z.record(z.string(), z.string()).optional(),
    prediction: z.record(z.string(), z.unknown()).optional(),
    serviceTier: z.enum(["auto", "flex", "priority", "default"]).optional(),
    textVerbosity: z.enum(["low", "medium", "high"]).optional(),
    promptCacheKey: z.string().optional(),
    promptCacheRetention: z.enum(["in_memory", "24h"]).optional(),
    safetyIdentifier: z.string().optional(),
  })
  .strict() satisfies z.ZodType<OpenAIProviderOptions>;

/**
 * Anthropic thinking config schema
 */
export const AnthropicThinkingConfigSchema = z
  .object({
    type: z.enum(["enabled", "disabled"]),
    budgetTokens: z.number().int().positive().optional(),
  })
  .strict() satisfies z.ZodType<AnthropicThinkingConfig>;

/**
 * Anthropic cache control schema
 */
export const AnthropicCacheControlSchema = z
  .object({
    type: z.literal("ephemeral"),
    ttl: z.string().optional(),
  })
  .strict() satisfies z.ZodType<AnthropicCacheControl>;

/**
 * Anthropic provider options schema
 */
export const AnthropicProviderOptionsSchema = z
  .object({
    thinking: AnthropicThinkingConfigSchema.optional(),
    cacheControl: AnthropicCacheControlSchema.optional(),
    disableParallelToolUse: z.boolean().optional(),
    sendReasoning: z.boolean().optional(),
    effort: z.enum(["high", "medium", "low"]).optional(),
    toolStreaming: z.boolean().optional(),
    structuredOutputMode: z.enum(["outputFormat", "jsonTool", "auto"]).optional(),
  })
  .strict() satisfies z.ZodType<AnthropicProviderOptions>;

/**
 * Google thinking config schema
 */
export const GoogleThinkingConfigSchema = z
  .object({
    thinkingLevel: z.enum(["low", "high"]).optional(),
    thinkingBudget: z.number().int().positive().optional(),
    includeThoughts: z.boolean().optional(),
  })
  .strict() satisfies z.ZodType<GoogleThinkingConfig>;

/**
 * Google safety setting schema
 */
export const GoogleSafetySettingSchema = z
  .object({
    category: z.string(),
    threshold: z.string(),
  })
  .strict() satisfies z.ZodType<GoogleSafetySetting>;

/**
 * Google provider options schema
 */
export const GoogleProviderOptionsSchema = z
  .object({
    thinkingConfig: GoogleThinkingConfigSchema.optional(),
    cachedContent: z.string().optional(),
    structuredOutputs: z.boolean().optional(),
    safetySettings: z.array(GoogleSafetySettingSchema).optional(),
    responseModalities: z.array(z.string()).optional(),
  })
  .strict() satisfies z.ZodType<GoogleProviderOptions>;

/**
 * DeepSeek thinking config schema
 */
export const DeepSeekThinkingConfigSchema = z
  .object({
    type: z.enum(["enabled", "disabled"]),
  })
  .strict() satisfies z.ZodType<DeepSeekThinkingConfig>;

/**
 * DeepSeek provider options schema
 */
export const DeepSeekProviderOptionsSchema = z
  .object({
    thinking: DeepSeekThinkingConfigSchema.optional(),
  })
  .strict() satisfies z.ZodType<DeepSeekProviderOptions>;

/**
 * OpenRouter provider options schema
 */
export const OpenRouterProviderOptionsSchema = z
  .object({
    provider: z.record(z.string(), z.unknown()).optional(),
    reasoning: z.record(z.string(), z.unknown()).optional(),
  })
  .strict() satisfies z.ZodType<OpenRouterProviderOptions>;

/**
 * Sno GPU provider options schema
 */
export const SnoGpuProviderOptionsSchema = z
  .object({
    enableThinking: z.boolean().optional(),
    thinkingBudget: z.number().int().positive().optional(),
    gpuPath: z.string().optional(),
  })
  .strict() satisfies z.ZodType<SnoGpuProviderOptions>;

/**
 * Combined provider options schema
 */
export const ProviderOptionsSchema = z
  .object({
    openai: OpenAIProviderOptionsSchema.optional(),
    anthropic: AnthropicProviderOptionsSchema.optional(),
    google: GoogleProviderOptionsSchema.optional(),
	    deepseek: DeepSeekProviderOptionsSchema.optional(),
	    openrouter: OpenRouterProviderOptionsSchema.optional(),
	    "sno-gpu": SnoGpuProviderOptionsSchema.optional(),
	    deepinfra: z.record(z.string(), z.unknown()).optional(),
	    novita: z.record(z.string(), z.unknown()).optional(),
	    together: z.record(z.string(), z.unknown()).optional(),
	  })
  .strict() satisfies z.ZodType<ProviderOptions>;

/**
 * Provider type schema
 */
export const ProviderSchema = z.enum([
  "openai",
  "anthropic",
  "google",
  "deepseek",
  "openrouter",
  "sno-gpu",
  "deepinfra",
  "novita",
  "together",
]) satisfies z.ZodType<Provider>;

/**
 * Timeout configuration schema (all values in seconds)
 */
export const TimeoutConfigSchema = z
  .object({
    /** Total time limit for the entire LLM call (seconds) */
    totalTime: z.number().positive().optional(),
    /** Max wait time for first chunk in streaming responses (seconds) */
    streamFirstChunkTime: z.number().positive().optional(),
  })
  .strict() satisfies z.ZodType<TimeoutConfig>;

/**
 * Caching configuration schema
 */
export const CachingConfigSchema = z
  .object({
    /** Caching strategy */
    strategy: z.enum(["native", "gateway", "disabled", "redis", "redis-or-memory", "memory"]),
    /** Cache key (required for native strategy) */
    key: z.string().optional(),
    /** TTL in seconds for response cache entries */
    ttl: z.number().positive().optional(),
    /** Maximum L1 cache entries */
    maxItems: z.number().int().positive().optional(),
  })
  .strict() satisfies z.ZodType<CachingConfig>;

/**
 * Full LLM config schema with cross-field validation
 *
 * Validates:
 * - All required fields present
 * - Unknown keys rejected (strict mode)
 * - Anthropic budgetTokens >= 1024 when thinking.type is "enabled"
 * - Native caching requires cache key
 * - Backwards compatibility with bypassGateway
 */
export const LLMConfigSchema = z
  .object({
    provider: ProviderSchema,
    model: z.string().min(1),
    common: CommonParamsSchema.optional(),
    providerOptions: ProviderOptionsSchema.optional(),
    timeout: TimeoutConfigSchema.optional(),
    description: z.string().optional(),
    deprecated: z.boolean().optional(),
    tags: z.array(z.string()).optional(),
    caching: CachingConfigSchema.optional(),
    bypassGateway: z.boolean().optional(),
  })
  .strict()
  .superRefine((data, ctx) => {
    // Cross-field validation: Anthropic budgetTokens minimum when thinking enabled
    if (
      data.provider === "anthropic" &&
      data.providerOptions?.anthropic?.thinking?.type === "enabled"
    ) {
      const budgetTokens = data.providerOptions.anthropic.thinking.budgetTokens;
      if (budgetTokens !== undefined && budgetTokens < ANTHROPIC_MIN_BUDGET_TOKENS) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `Anthropic budgetTokens must be >= ${ANTHROPIC_MIN_BUDGET_TOKENS} when thinking is enabled, got ${budgetTokens}`,
          path: ["providerOptions", "anthropic", "thinking", "budgetTokens"],
        });
      }
    }

    // LH: caching.key is now optional when using native strategy.
    // The native cache key is provided via caching.key
    // (typically from Promptix). Config key is used as fallback if call-time key is not provided.

    // Keep mixed legacy/new configs loadable during migration. Runtime callers
    // can prefer caching.strategy while still seeing the deprecated flag.
  }) satisfies z.ZodType<LLMConfig>;

// =============================================================================
// MDA LOADING
// =============================================================================

export type MdaConfigLoadOptions = LoadMdaSourceOptions;

const NetworkRequirementSchema = z.union([
  z.literal("none"),
  z.literal("local"),
  z.literal("public"),
  z.array(z.string()),
]);

const MdaRequiresSchema = z
  .object({
    network: NetworkRequirementSchema.optional(),
  })
  .catchall(z.unknown());

const LLMixMdaCommonSchema = CommonParamsSchema.extend({
  provider: ProviderSchema,
  model: z.string().min(1),
}).strict();

const LLMixMdaNamespaceSchema = z
  .object({
    module: z.string().min(1).optional(),
    preset: z.string().min(1).optional(),
    common: LLMixMdaCommonSchema,
    providerOptions: ProviderOptionsSchema.optional(),
    timeout: TimeoutConfigSchema.optional(),
    description: z.string().optional(),
    deprecated: z.boolean().optional(),
    tags: z.array(z.string()).optional(),
    caching: CachingConfigSchema.optional(),
    bypassGateway: z.boolean().optional(),
  })
  .strict();

export const LLMixMdaPresetSchema = z
  .object({
    name: z.string().min(1),
    description: z.string().min(1),
    license: z.string().optional(),
    compatibility: z.string().optional(),
    "allowed-tools": z.string().optional(),
    metadata: z
      .object({
        "snoai-llmix": LLMixMdaNamespaceSchema,
      })
      .catchall(z.unknown()),
    integrity: z.record(z.string(), z.unknown()).optional(),
    signatures: z.array(z.record(z.string(), z.unknown())).optional(),
    "doc-id": z.string().optional(),
    title: z.string().optional(),
    version: z.string().optional(),
    requires: MdaRequiresSchema.optional(),
    "depends-on": z.array(z.record(z.string(), z.unknown())).optional(),
    author: z.string().optional(),
    tags: z.array(z.string()).optional(),
    "created-date": z.string().optional(),
    "updated-date": z.string().optional(),
    relationships: z.array(z.record(z.string(), z.unknown())).optional(),
  })
  .strict();

export type LLMixMdaPreset = z.infer<typeof LLMixMdaPresetSchema>;

function rejectLegacyConfigPath(configPath: string): void {
  const lowerPath = configPath.toLowerCase();
  if (lowerPath.endsWith(".yaml") || lowerPath.endsWith(".yml")) {
    throw new InvalidConfigError(
      `TypeScript LLMix presets use .mda files; YAML presets are no longer supported: ${configPath}`
    );
  }
}

function ensureMdaConfigPath(configPath: string): void {
  rejectLegacyConfigPath(configPath);
  if (!configPath.toLowerCase().endsWith(".mda")) {
    throw new InvalidConfigError(`TypeScript LLMix presets must use .mda files: ${configPath}`);
  }
}

function mapMdaLoadError(error: unknown, filePath: string): Error {
  if (error instanceof Error && "code" in error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return new ConfigNotFoundError(`Config file not found: ${filePath}`);
    }
    if (code === "EACCES") {
      return new ConfigAccessError(`Permission denied reading config file: ${filePath}`);
    }
  }
  return error instanceof Error ? error : new Error(String(error));
}

function projectMdaPresetToConfig(preset: LLMixMdaPreset, sourcePath: string): LLMConfig {
  const namespace = preset.metadata["snoai-llmix"];
  const { provider, model, ...common } = namespace.common;

  const config: LLMConfig = {
    provider,
    model,
  };

  if (Object.keys(common).length > 0) {
    config.common = common;
  }
  if (namespace.providerOptions !== undefined) {
    config.providerOptions = namespace.providerOptions;
  }
  if (namespace.timeout !== undefined) {
    config.timeout = namespace.timeout;
  }
  config.description = namespace.description ?? preset.description;
  if (namespace.deprecated !== undefined) {
    config.deprecated = namespace.deprecated;
  }
  const tags = namespace.tags ?? preset.tags;
  if (tags !== undefined) {
    config.tags = tags;
  }
  if (namespace.caching !== undefined) {
    config.caching = namespace.caching;
  }
  if (namespace.bypassGateway !== undefined) {
    config.bypassGateway = namespace.bypassGateway;
  }

  const result = LLMConfigSchema.safeParse(config);
  if (!result.success) {
    const issues = result.error.issues
      .map((issue) => `  - ${issue.path.join(".")}: ${issue.message}`)
      .join("\n");
    throw new InvalidConfigError(`Projected MDA config validation failed for ${sourcePath}:\n${issues}`);
  }

  return result.data;
}

/**
 * Load and validate an MDA Source Mode LLMix preset from an explicit file path.
 */
export async function loadMdaConfig(
  configPath: string,
  options?: MdaConfigLoadOptions
): Promise<LLMConfig> {
  ensureMdaConfigPath(configPath);
  const filePath = resolve(configPath);
  await verifyPathContainmentAsync(filePath, dirname(filePath));

  let preset: LLMixMdaPreset;
  try {
    preset = await loadMdaSource(filePath, LLMixMdaPresetSchema, options);
  } catch (error) {
    throw mapMdaLoadError(error, filePath);
  }

  return projectMdaPresetToConfig(preset, filePath);
}

/**
 * Load a preset file from `{baseDir}/{name}.mda`.
 */
export async function loadMdaConfigPreset(
  name: string,
  baseDir: string,
  options?: MdaConfigLoadOptions
): Promise<LLMConfig> {
  rejectLegacyConfigPath(name);

  const presetFile = basename(name);
  const preset = presetFile.endsWith(".mda") ? presetFile.slice(0, -4) : presetFile;

  validatePreset(preset);

  const presetsDir = resolve(baseDir);
  const filePath = join(presetsDir, `${preset}.mda`);
  await verifyPathContainmentAsync(filePath, presetsDir);
  return loadMdaConfig(filePath, options);
}

/**
 * Load and validate an MDA LLMix preset by config directory, module, and preset id.
 */
export async function loadMdaConfigFromFile(
  configDir: string,
  module: string,
  preset: string,
  options?: MdaConfigLoadOptions
): Promise<LLMConfig> {
  validateModule(module);
  validatePreset(preset);

  const filePath = buildMdaConfigFilePath(configDir, module, preset);
  await verifyPathContainmentAsync(filePath, configDir);
  return loadMdaConfig(filePath, options);
}
