/**
 * YAML-based LLM Config Loader
 *
 * Loads and validates LLM configuration from YAML files.
 * Security: Safe YAML parsing (no custom tags), path traversal protection,
 * strict schema validation (rejects unknown keys).
 */

import { realpathSync } from "node:fs";
import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import { parse as parseYaml } from "yaml";
import { z } from "zod";
import {
  ANTHROPIC_MIN_BUDGET_TOKENS,
  type AnthropicCacheControl,
  type AnthropicProviderOptions,
  type AnthropicThinkingConfig,
  type CachingConfig,
  type CommonParams,
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
  type Provider,
  type ProviderOptions,
  SecurityError,
  type TimeoutConfig,
  VALID_MODULE_PATTERN,
  VALID_PROFILE_PATTERN,
  VALID_SCOPE_PATTERN,
  VALID_USER_ID_PATTERN,
} from "./types";

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
 * Validate profile name against security rules
 *
 * @throws Error if profile name is invalid
 */
export function validateProfile(profile: string): void {
  if (!profile) {
    throw new Error("Profile name cannot be empty");
  }

  if (profile.length > 64) {
    throw new Error(`Profile name too long: ${profile.length} > 64`);
  }

  // Security: Prevent path traversal
  const dangerousChars = ["/", "\\", "..", "~", "$", "`"];
  if (dangerousChars.some((char) => profile.includes(char))) {
    throw new SecurityError(`Invalid characters in profile: ${profile}`);
  }

  if (!VALID_PROFILE_PATTERN.test(profile)) {
    throw new Error(
      `Invalid profile format: ${profile}. ` +
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
 * Path format: {configDir}/{module}/{profile}.v{version}.yaml
 * Note: scope is NOT part of the file path (used for cascade resolution)
 *
 * @param configDir - Base config directory
 * @param module - Module name (e.g., "hrkg", "_default")
 * @param profile - Profile name (e.g., "extraction", "_base")
 * @param version - Config version number
 * @returns Resolved file path
 */
export function buildConfigFilePath(
  configDir: string,
  module: string,
  profile: string,
  version: number
): string {
  const filename = `${profile}.v${version}.yaml`;
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
 * Common AI SDK v5 parameters schema
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
    logitBias: z.record(z.number().int(), z.number()).optional(),
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
 * Combined provider options schema
 */
export const ProviderOptionsSchema = z
  .object({
    openai: OpenAIProviderOptionsSchema.optional(),
    anthropic: AnthropicProviderOptionsSchema.optional(),
    google: GoogleProviderOptionsSchema.optional(),
    deepseek: DeepSeekProviderOptionsSchema.optional(),
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
]) satisfies z.ZodType<Provider>;

/**
 * Timeout configuration schema (all values in minutes)
 */
export const TimeoutConfigSchema = z
  .object({
    /** Total time limit for the entire LLM call (minutes) */
    totalTime: z.number().positive().optional(),
    /** Max wait time for first chunk in streaming responses (minutes) */
    streamFirstChunkTime: z.number().positive().optional(),
  })
  .strict() satisfies z.ZodType<TimeoutConfig>;

/**
 * Caching configuration schema
 */
export const CachingConfigSchema = z
  .object({
    /** Caching strategy */
    strategy: z.enum(["native", "gateway", "disabled"]),
    /** Cache key (required for native strategy) */
    key: z.string().optional(),
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
    // The cache key can be provided at call time via CallOptions.promptCacheKey
    // (typically from Promptix). Config key is used as fallback if call-time key is not provided.

    // Warn if both bypassGateway and caching are set (potential conflict)
    if (data.bypassGateway !== undefined && data.caching !== undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Both bypassGateway (deprecated) and caching are set. Use caching.strategy instead of bypassGateway.",
        path: ["bypassGateway"],
      });
    }
  }) satisfies z.ZodType<LLMConfig>;

// =============================================================================
// YAML LOADING
// =============================================================================

/**
 * Load and validate LLM config from YAML file
 *
 * Security features:
 * - Safe YAML parsing (no custom tags, no code execution)
 * - Path traversal protection
 * - Strict schema validation (unknown keys rejected)
 *
 * @param configDir - Base config directory
 * @param module - Module name (e.g., "hrkg", "_default")
 * @param profile - Profile name (e.g., "extraction", "_base")
 * @param version - Config version number
 * @returns Validated LLMConfig
 * @throws ConfigNotFoundError if file doesn't exist
 * @throws InvalidConfigError if YAML parsing or schema validation fails
 * @throws SecurityError if path traversal detected
 */
export async function loadConfigFromFile(
  configDir: string,
  module: string,
  profile: string,
  version: number
): Promise<LLMConfig> {
  // Validate inputs
  validateModule(module);
  validateProfile(profile);
  validateVersion(version);

  // Build and verify file path
  const filePath = buildConfigFilePath(configDir, module, profile, version);
  await verifyPathContainmentAsync(filePath, configDir);

  // Read file
  let content: string;
  try {
    content = await readFile(filePath, "utf-8");
  } catch (error) {
    if (error instanceof Error && "code" in error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "ENOENT") {
        throw new ConfigNotFoundError(
          `Config file not found: ${filePath} (module=${module}, profile=${profile}, version=${version})`
        );
      }
      if (code === "EACCES") {
        throw new ConfigNotFoundError(`Permission denied reading config file: ${filePath}`);
      }
    }
    throw error;
  }

  // Parse YAML with safe mode (default - no custom tags)
  // The yaml package uses CORE schema by default which is safe
  let parsed: unknown;
  try {
    parsed = parseYaml(content);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new InvalidConfigError(`YAML parsing failed for ${filePath}: ${message}`);
  }

  // Validate against schema
  const result = LLMConfigSchema.safeParse(parsed);
  if (!result.success) {
    const issues = result.error.issues
      .map((issue) => `  - ${issue.path.join(".")}: ${issue.message}`)
      .join("\n");
    throw new InvalidConfigError(`Schema validation failed for ${filePath}:\n${issues}`);
  }

  return result.data;
}
