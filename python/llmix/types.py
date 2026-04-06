"""
LLMix Type Definitions and Validation

Contains exception classes, type aliases, constants, and validation functions.
Mirrors TypeScript implementation in package/llmix/src/types.ts.
"""

import re
from typing import Any, Awaitable, Literal, Protocol, TypedDict

try:
    from lib.infra.validation import DANGEROUS_CHARS as DANGEROUS_CHARS
    from lib.infra.validation import VALID_USER_ID_PATTERN as VALID_USER_ID_PATTERN
    from lib.infra.validation import validate_user_id as validate_user_id
except ImportError:
    DANGEROUS_CHARS: list[str] = ["/", "\\", "..", "~", "$", "`"]
    VALID_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

    def validate_user_id(user_id: str | None) -> bool:
        """Validate user ID format without the shared infra package."""
        if user_id is None:
            return False
        if len(user_id) > 64:
            return False
        if any(char in user_id for char in DANGEROUS_CHARS):
            return False
        return bool(VALID_USER_ID_PATTERN.match(user_id))

# =============================================================================
# EXCEPTIONS
# =============================================================================


class LLMConfigError(Exception):
    """Base exception for LLM config operations"""


class ConfigNotFoundError(LLMConfigError):
    """Raised when a config cannot be found in the cascade"""


class InvalidConfigError(LLMConfigError):
    """Raised when a config file is invalid (schema validation failed)"""


class SecurityError(LLMConfigError):
    """Raised when a security violation is detected (e.g., path traversal)"""


# =============================================================================
# CACHING STRATEGY
# =============================================================================

CachingStrategy = Literal["native", "gateway", "disabled", "redis", "redis-or-memory", "memory"]

ResponseCacheStrategy = Literal["redis", "redis-or-memory", "memory"]
"""Strategies that activate the two-tier response cache."""

CacheHitTier = Literal["l1", "l2"]
"""Cache hit tier indicator."""

FallbackTrigger = Literal["timeout", "connection_error", "5xx"]


class FallbackConfig(TypedDict, total=False):
    """Fallback configuration for automatic provider failover."""

    preset: str
    """Fallback preset string (e.g., '_default:novita_qwen35_27b')"""

    on: list[FallbackTrigger]
    """Trigger conditions (default: all three: timeout, connection_error, 5xx)"""


"""
Caching strategy for LLM calls:

- "native": Provider's native caching (OpenAI/Anthropic prompt caching via Helicone)
  - 90% cost savings on cached tokens
  - Requires cache key to group related prompts
  - Routes through Helicone for OpenAI (http://sno-main-1:8585)
  - Only for LLM calls (not embeddings)

- "gateway": AI Gateway response caching (CF AI Gateway)
  - Exact match only
  - Good for identical requests
  - Works for all providers

- "disabled": No caching
  - Always fresh calls
  - Useful for real-time or non-repeatable prompts
"""


class CachingConfig(TypedDict, total=False):
    """
    Caching configuration from YAML config.

    Mirrors TypeScript CachingConfig in package/llmix/src/types.ts.
    """

    strategy: CachingStrategy
    """Caching strategy: native (Helicone), gateway (CF AI Gateway), or disabled"""

    key: str
    """
    Cache key for native strategy.
    Required for native strategy - groups related prompts together.
    Optional for gateway/disabled strategies.
    Example: "extraction-v1", "search-2024"
    """

    ttl: int
    """TTL in seconds for response cache entries (default: 3600). Applies to L1 and L2."""

    max_items: int
    """Maximum L1 cache entries (default: 1000)."""


# =============================================================================
# PROVIDER TYPES
# =============================================================================

Provider = Literal["openai", "anthropic", "google", "deepseek", "deepinfra", "together", "novita", "snogpu"]
ProviderOrUnknown = Literal["openai", "anthropic", "google", "deepseek", "deepinfra", "together", "novita", "snogpu", "unknown"]


# =============================================================================
# TIMEOUT CONFIGURATION
# =============================================================================


class TimeoutConfig(TypedDict, total=False):
    """
    Timeout configuration for LLM calls (all values in seconds).

    Per-preset timeout allows reasoning models to have longer timeouts
    than fast models, without affecting global defaults.
    """

    total_time: float
    """Total time limit for the entire LLM call (seconds). Default: 120"""

    stream_first_chunk_time: float
    """Max wait time for first chunk in streaming responses (seconds)."""


# =============================================================================
# COMMON PARAMS (AI SDK v6 aligned)
# =============================================================================


class CommonParams(TypedDict, total=False):
    """
    Common AI SDK v6 parameters.

    These map directly to generateText/streamText params.
    """

    max_output_tokens: int
    """Max tokens to generate"""

    temperature: float
    """Temperature 0.0-2.0 (don't use with topP)"""

    top_p: float
    """Top-p sampling 0.0-1.0 (don't use with temperature)"""

    top_k: int
    """Sample from top K options"""

    presence_penalty: float
    """Reduce repetition of existing info"""

    frequency_penalty: float
    """Reduce reuse of identical phrases"""

    stop_sequences: list[str]
    """Sequences that halt generation"""

    seed: int
    """Seed for deterministic results"""

    max_retries: int
    """Retry attempts (default: 2)"""

    enable_thinking: bool
    """Enable/disable thinking mode (provider-specific, e.g. Qwen3 on DeepInfra)"""

    keep_thinking_output: bool
    """Preserve <think>...</think> blocks in response content.
    When True, thinking blocks are NOT stripped and thinking_content is not populated.
    Default: False (thinking blocks are stripped automatically)."""


# =============================================================================
# PROVIDER-SPECIFIC OPTIONS
# =============================================================================


class OpenAIProviderOptions(TypedDict, total=False):
    """OpenAI-specific provider options."""

    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"]
    parallel_tool_calls: bool
    user: str
    logprobs: bool | int
    logit_bias: dict[int, int]
    structured_outputs: bool
    strict_json_schema: bool
    max_completion_tokens: int
    store: bool
    metadata: dict[str, str]
    service_tier: Literal["auto", "flex", "priority", "default"]
    text_verbosity: Literal["low", "medium", "high"]
    prompt_cache_key: str
    prompt_cache_retention: Literal["in_memory", "24h"]


class AnthropicThinkingConfig(TypedDict, total=False):
    """Anthropic thinking configuration."""

    type: Literal["enabled", "disabled"]
    budget_tokens: int


class AnthropicProviderOptions(TypedDict, total=False):
    """Anthropic-specific provider options."""

    thinking: AnthropicThinkingConfig
    cache_control: dict[str, Any]
    disable_parallel_tool_use: bool
    send_reasoning: bool
    effort: Literal["high", "medium", "low"]
    tool_streaming: bool


class GoogleThinkingConfig(TypedDict, total=False):
    """Google thinking configuration."""

    thinking_level: Literal["low", "high"]
    thinking_budget: int
    include_thoughts: bool


class GoogleProviderOptions(TypedDict, total=False):
    """Google-specific provider options."""

    thinking_config: GoogleThinkingConfig
    cached_content: str
    structured_outputs: bool
    safety_settings: list[dict[str, str]]
    response_modalities: list[str]


class DeepSeekThinkingConfig(TypedDict, total=False):
    """DeepSeek thinking configuration."""

    type: Literal["enabled", "disabled"]


class DeepSeekProviderOptions(TypedDict, total=False):
    """DeepSeek-specific provider options."""

    thinking: DeepSeekThinkingConfig


class DeepInfraProviderOptions(TypedDict, total=False):
    """DeepInfra-specific provider options."""

    enable_thinking: bool
    """Enable thinking mode for supported models (e.g. Qwen3). Default: False."""

    thinking_budget: int
    """Token budget for thinking/reasoning. Only applies when enable_thinking=True."""


class TogetherProviderOptions(TypedDict, total=False):
    """Together AI-specific provider options."""


class NovitaProviderOptions(TypedDict, total=False):
    """Novita AI-specific provider options."""

    enable_thinking: bool
    """Enable thinking mode for supported Novita models. Default: False."""

    thinking_budget: int
    """Token budget for thinking/reasoning. Only applies when enable_thinking=True."""


class SnogpuProviderOptions(TypedDict, total=False):
    """Sno on-prem GPU-specific provider options."""

    enable_thinking: bool
    """Enable thinking mode for Qwen3.5. Default: False."""

    thinking_budget: int
    """Token budget for thinking/reasoning. Only applies when enable_thinking=True."""

    gpu_path: str
    """GPU routing path: 'extract' (GPU 0, SMR) or 'reason' (GPU 1, EKG). Omit for legacy /v1."""


class ProviderOptions(TypedDict, total=False):
    """Union type for all provider options."""

    openai: OpenAIProviderOptions
    anthropic: AnthropicProviderOptions
    google: GoogleProviderOptions
    deepseek: DeepSeekProviderOptions
    deepinfra: DeepInfraProviderOptions
    together: TogetherProviderOptions
    novita: NovitaProviderOptions
    snogpu: SnogpuProviderOptions


# =============================================================================
# LLM CONFIG SCHEMA
# =============================================================================


class LLMConfig(TypedDict, total=False):
    """
    Full LLM configuration schema.

    This schema mirrors AI SDK v6 exactly - values are passed directly
    to LLM calls without translation.
    """

    provider: Provider
    """LLM provider (required)"""

    model: str
    """Provider-specific model ID (required)"""

    common: CommonParams
    """Common AI SDK v6 parameters"""

    provider_options: ProviderOptions
    """Provider-specific options"""

    timeout: TimeoutConfig
    """Timeout configuration for this preset"""

    description: str
    """Human-readable description (metadata)"""

    deprecated: bool
    """Mark config as deprecated (metadata)"""

    tags: list[str]
    """Tags for organization/filtering (metadata)"""

    caching: CachingConfig
    """Caching strategy for this preset"""

    bypass_gateway: bool
    """DEPRECATED: Use caching.strategy instead"""

    helicone: bool
    """
    Enable Helicone proxy routing for logging and observability (default: true).
    Set to false to bypass Helicone and call provider APIs directly.
    When enabled, all LLM calls are logged via Helicone regardless of caching strategy.
    """

    fallback: FallbackConfig
    """Optional fallback configuration for automatic provider failover on failure."""


class ResolvedLLMConfig(LLMConfig):
    """
    Resolved LLM configuration.

    Represents a fully parsed and validated config ready for use.
    """

    config_id: str
    """ConfigId that was resolved (canonical format)"""

    scope: str
    """The scope used in resolution"""

    module: str
    """The module used in resolution"""

    preset: str
    """The preset used in resolution"""

    version: int
    """The version used in resolution"""


# =============================================================================
# EXPERIMENT CONFIG
# =============================================================================


class ExperimentConfig(TypedDict):
    """Experiment configuration from Redis (100% toggle behavior).

    Redis key format: experiment:llm:{module}:{preset}
    Example: experiment:llm:hrkg:extraction

    When enabled=True, 100% traffic goes to experiment version.
    When enabled=False, 100% traffic goes to control (v1).
    """

    enabled: bool
    """Whether the experiment is currently active (100% toggle)"""

    version: int
    """Version to use when experiment is enabled (e.g., 2 for v2)"""

    enabledAt: str
    """ISO timestamp when experiment was enabled"""


# =============================================================================
# TELEMETRY TYPES
# =============================================================================


class TelemetryContext(TypedDict, total=False):
    """Telemetry context for LLM calls."""

    user_id: str
    """User identifier"""

    workspace_id: str
    """Workspace identifier"""

    project_id: str
    """Project identifier"""

    feature_name: str
    """Feature name for tracking"""

    trace_id: str
    """Trace ID for correlation across services"""

    session_id: str
    """Session ID for grouping related LLM calls"""

    conversation_id: str
    """Conversation/thread ID for multi-turn conversations"""

    turn_number: int
    """Turn number in conversation (1, 2, 3...)"""

    experiment_id: str
    """Experiment ID for A/B testing"""

    variant_id: str
    """Variant ID within experiment (control/treatment)"""

    prompt_version: str
    """Prompt template version for iteration tracking"""

    fallback_used: bool
    """Whether a fallback was used"""

    fallback_reason: str
    """Reason for fallback if used"""


class LLMCallEventData(TypedDict, total=False):
    """LLM call event data for telemetry tracking."""

    config_id: str
    """Config ID that was resolved"""

    provider: Provider
    """Provider (openai, anthropic, google, deepseek)"""

    model: str
    """Model used"""

    module: str
    """Module from config resolution"""

    preset: str
    """Preset from config resolution"""

    scope: str
    """Scope from config resolution"""

    version: int
    """Config version"""

    input_tokens: int
    """Input tokens consumed"""

    output_tokens: int
    """Output tokens generated"""

    total_tokens: int
    """Total tokens"""

    latency_ms: int
    """Latency in milliseconds"""

    success: bool
    """Whether the call succeeded"""

    error_message: str
    """Error message if failed"""

    context: TelemetryContext
    """Telemetry context passed by caller"""

    messages: list[Any]
    """Input messages (for tracing systems that capture payloads)"""

    output: str
    """Output text (for tracing systems that capture payloads)"""


class LLMixTelemetryProvider(Protocol):
    """
    Telemetry provider interface for dependency injection.

    Implement this protocol to integrate LLMix with your telemetry system.
    """

    def track_llm_call(self, event: LLMCallEventData) -> Awaitable[None]:
        """
        Track an LLM call event.

        Called after every LLM call (success or failure).
        Implementation should be best-effort (don't throw).
        """
        ...


# =============================================================================
# RUNTIME OVERRIDES
# =============================================================================


class RuntimeOverrides(TypedDict, total=False):
    """Runtime overrides for LLM calls."""

    model: str
    """Override model (transitional support)"""

    common: CommonParams
    """Override common parameters"""

    provider_options: ProviderOptions
    """Override provider options"""

    bypass_gateway: bool
    """Bypass AI Gateway for native provider features"""


# =============================================================================
# CALL OPTIONS
# =============================================================================


class _CallOptionsRequired(TypedDict):
    """Required fields for LLMClient.call()."""

    preset: str
    """
    Preset string in format "module:preset" or just "preset"
    - "hrkg:extraction" -> module=hrkg, preset=extraction
    - "extraction" -> module=_default, preset=extraction
    """

    messages: list[Any]
    """Messages to send to the LLM"""


class CallOptions(_CallOptionsRequired, total=False):
    """Options for LLMClient.call()."""

    scope: str
    """Deployment scope (default: defaultScope from config)"""

    user_id: str
    """User ID for per-user config overrides"""

    version: int
    """Config version (default: 1)"""

    overrides: RuntimeOverrides
    """Runtime overrides (merged with config)"""

    telemetry: TelemetryContext
    """Telemetry context"""

    prompt_cache_key: str
    """
    Cache key for native prompt caching (OpenAI/Anthropic).
    Usually obtained from Promptix: prompt.promptCacheKey
    Format: "{category}:{promptName}:v{version}"
    """

    response_format: dict[str, Any]
    """Response format for structured output (e.g. {"type": "json_schema", "schema": {...}})"""

    # Python-only: parse_json and parsed_data are not mirrored in package/llmix/src/types.ts
    # TypeScript services use AI SDK structured outputs instead
    parse_json: bool
    """When True, LLMix parses response content as JSON (with json_repair + retry). Default False."""


# =============================================================================
# RESPONSE TYPES
# =============================================================================


class LLMUsage(TypedDict, total=False):
    """Token usage statistics from LLM call."""

    input_tokens: int
    """Input/prompt tokens consumed"""

    output_tokens: int
    """Output/completion tokens generated"""

    total_tokens: int
    """Total tokens (input + output)"""

    cached_input_tokens: int
    """Cached input tokens (provider-dependent, may be undefined)"""


class LLMResponse(TypedDict, total=False):
    """Response from LLMClient.call()."""

    content: str
    """Generated content"""

    model: str
    """Model used for generation"""

    provider: ProviderOrUnknown
    """Provider used for generation. 'unknown' when config load fails."""

    usage: LLMUsage
    """Token usage statistics"""

    config: ResolvedLLMConfig
    """The resolved config that was used. Undefined when config load fails."""

    success: bool
    """Whether the call succeeded"""

    error: str
    """Error message if success is false"""

    retryable: bool
    """Whether a failed call is retryable"""

    output: list[Any]
    """Full output array for Response API caching optimization"""

    tool_calls: list[Any]
    """Tool calls if present"""

    fallback_used: bool
    """Whether a fallback provider was used for this response"""

    fallback_reason: str
    """Reason for fallback if used (e.g., 'timeout', 'connection_error', '5xx')"""

    parsed_data: dict[str, Any] | list[Any] | None
    """Parsed JSON when parse_json=True. dict or list only (scalars trigger retry)."""

    thinking_content: str
    """Captured thinking content stripped from response (when keep_thinking_output is False)."""

    cache_hit: CacheHitTier
    """Indicates which cache tier served this response, if any."""


# =============================================================================
# CONFIG CAPABILITIES
# =============================================================================


class ConfigCapabilities(TypedDict):
    """Config capabilities for runtime decisions."""

    provider: Provider
    """The provider (openai, anthropic, google, deepseek)"""

    is_proprietary: bool
    """Whether the provider is proprietary (not open-source)"""

    supports_openai_batch: bool
    """
    Whether the model supports OpenAI Batch API.
    True IFF: provider === 'openai' AND model is in BATCH_CAPABLE_MODELS
    """


class ResolvedConfigWithCapabilities(TypedDict):
    """Result from get_resolved_config() in LLMClient."""

    config: ResolvedLLMConfig
    """The resolved configuration"""

    capabilities: ConfigCapabilities
    """Capabilities derived from the config"""


class ResolvedConfigResult(TypedDict):
    """Result of config resolution for dry-run testing (from LLMConfigLoader)."""

    config: dict[str, Any]
    """The resolved configuration dictionary"""

    version: int
    """The resolved version number"""

    experiment_active: bool
    """Whether an A/B experiment was active"""


# =============================================================================
# CLIENT CONFIG
# =============================================================================


class ProviderUrlConfig(TypedDict, total=False):
    """Provider base URL configuration."""

    openai_base_url: str
    """OpenAI base URL override"""

    anthropic_base_url: str
    """Anthropic base URL override"""

    gemini_base_url: str
    """Google/Gemini base URL override"""

    openrouter_base_url: str
    """OpenRouter base URL override"""

    openrouter_api_key: str
    """OpenRouter API key (falls back to env var)"""


class HeliconeConfig(TypedDict, total=False):
    """Helicone configuration for LLM call logging and observability."""

    api_key: str
    """Helicone API key (required for native caching)"""

    base_url: str
    """Helicone base URL (default: http://sno-main-1:8585)"""

    environment: str
    """Environment name for Helicone property headers (default: dev)"""


class ApiKeysConfig(TypedDict, total=False):
    """API keys configuration for LLM providers."""

    openai: str
    """OpenAI API key"""

    anthropic: str
    """Anthropic API key"""

    google: str
    """Google Generative AI API key"""

    openrouter: str
    """OpenRouter API key (used for DeepSeek)"""

    deepinfra: str
    """DeepInfra API key"""

    together: str
    """Together AI API key"""

    novita: str
    """Novita AI API key"""

    snogpu: str
    """Sno on-prem GPU service secret (INTERNAL_SERVICE_SECRET)"""


# =============================================================================
# CACHE STATS
# =============================================================================


class LRUCacheStats(TypedDict):
    """Statistics for LRU cache."""

    size: int
    """Current number of items in cache"""

    max_size: int
    """Maximum cache size"""

    hits: int
    """Cache hit count"""

    misses: int
    """Cache miss count"""

    hit_rate: float
    """Hit rate percentage (0-100)"""


class CacheStats(TypedDict):
    """Combined cache statistics for LLMConfigLoader."""

    local_cache: LRUCacheStats
    """LRU cache statistics"""

    redis_available: bool
    """Whether Redis is currently available"""


# =============================================================================
# VALIDATION CONSTANTS
# =============================================================================

# Pattern for valid module names
# Allows: _default, or lowercase alphanumeric with underscores starting with letter
VALID_MODULE_PATTERN = re.compile(r"^(_default|[a-z][a-z0-9_]{0,63})$")

# Pattern for valid preset names
# Allows: _base (and _base_*), or lowercase alphanumeric with underscores starting with letter
VALID_PRESET_PATTERN = re.compile(r"^(_base[a-z0-9_]*|[a-z][a-z0-9_]{0,63})$")

# Pattern for valid scope names
# Allows: _default, or lowercase alphanumeric with underscores/hyphens starting with letter
VALID_SCOPE_PATTERN = re.compile(r"^(_default|[a-z][a-z0-9_-]{0,63})$")

# Pattern for valid user IDs (re-exported from lib.infra.validation)

# Version bounds
MIN_VERSION = 1
MAX_VERSION = 9999

# Valid providers list
VALID_PROVIDERS: tuple[Provider, ...] = ("openai", "anthropic", "google", "deepseek", "deepinfra", "together", "novita", "snogpu")

# Anthropic minimum budget tokens for extended thinking
ANTHROPIC_MIN_BUDGET_TOKENS = 1024

# OpenAI prompt cache minimum token threshold
OPENAI_PROMPT_CACHE_MIN_TOKENS = 1024

# Characters that could enable path traversal attacks (re-exported from lib.infra.validation)

# Models that support OpenAI Batch API
BATCH_CAPABLE_MODEL_PATTERNS = [r"^gpt-4", r"^gpt-5", r"^o1", r"^o3"]


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================


def validate_module(module: str) -> None:
    """
    Validate module name against security rules.

    Args:
        module: Module name to validate

    Raises:
        SecurityError: If name contains dangerous patterns
        ValueError: If name format is invalid
    """
    if not module:
        raise ValueError("Module name cannot be empty")

    if len(module) > 64:
        raise ValueError(f"Module name too long: {len(module)} > 64")

    # Security: Prevent path traversal
    if any(char in module for char in DANGEROUS_CHARS):
        raise SecurityError(f"Invalid characters in module: {module!r}")

    if not VALID_MODULE_PATTERN.match(module):
        raise ValueError(
            f"Invalid module format: {module}. "
            "Must be '_default' or start with lowercase letter and contain only "
            "lowercase letters, numbers, and underscores"
        )


def validate_preset(preset: str) -> None:
    """
    Validate preset name against security rules.

    Args:
        preset: Preset name to validate

    Raises:
        SecurityError: If name contains dangerous patterns
        ValueError: If name format is invalid
    """
    if not preset:
        raise ValueError("Preset name cannot be empty")

    if len(preset) > 64:
        raise ValueError(f"Preset name too long: {len(preset)} > 64")

    # Security: Prevent path traversal
    if any(char in preset for char in DANGEROUS_CHARS):
        raise SecurityError(f"Invalid characters in preset: {preset!r}")

    if not VALID_PRESET_PATTERN.match(preset):
        raise ValueError(
            f"Invalid preset format: {preset}. "
            "Must be '_base*' or start with lowercase letter and contain only "
            "lowercase letters, numbers, and underscores"
        )


def validate_scope(scope: str) -> None:
    """
    Validate scope name against security rules.

    Args:
        scope: Scope name to validate

    Raises:
        SecurityError: If name contains dangerous patterns
        ValueError: If name format is invalid
    """
    if not scope:
        raise ValueError("Scope name cannot be empty")

    if len(scope) > 64:
        raise ValueError(f"Scope name too long: {len(scope)} > 64")

    # Security: Prevent path traversal
    if any(char in scope for char in DANGEROUS_CHARS):
        raise SecurityError(f"Invalid characters in scope: {scope!r}")

    if not VALID_SCOPE_PATTERN.match(scope):
        raise ValueError(
            f"Invalid scope format: {scope}. "
            "Must be '_default' or start with lowercase letter and contain only "
            "lowercase letters, numbers, underscores, and hyphens"
        )


# validate_user_id re-exported from lib.infra.validation at top of file


def validate_version(version: int) -> None:
    """
    Validate version number.

    Args:
        version: Version to validate

    Raises:
        ValueError: If version is out of valid range
        TypeError: If version is not an integer
    """
    if isinstance(version, bool) or not isinstance(version, int):
        raise TypeError(f"Version must be an integer, got {type(version).__name__}")

    if not MIN_VERSION <= version <= MAX_VERSION:
        raise ValueError(f"Version {version} out of valid range [{MIN_VERSION}, {MAX_VERSION}]")
