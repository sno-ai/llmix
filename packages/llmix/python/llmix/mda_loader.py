"""MDA config loading and projection utilities for LLMix."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from snoai_mda_config import MdaConfigError, load_mda_source

from llmix.types import (
    LLMConfig,
    ConfigAccessError,
    ConfigNotFoundError,
    InvalidConfigError,
    SecurityError,
    validate_module,
    validate_preset,
)

__all__ = [
    "MdaConfigLoadOptions",
    "build_mda_config_file_path",
    "load_mda_config",
    "load_mda_config_from_file",
    "load_mda_config_preset",
]

# MDA authoring uses the TypeScript public field names. Python returns the
# runtime config in the existing snake_case shape.
_CAMEL_TO_SNAKE: dict[str, str] = {
    "baseUrl": "base_url",
    "maxOutputTokens": "max_output_tokens",
    "maxRetries": "max_retries",
    "topP": "top_p",
    "topK": "top_k",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
    "stopSequences": "stop_sequences",
    "totalTime": "total_time",
    "streamFirstChunkTime": "stream_first_chunk_time",
    "providerOptions": "provider_options",
    "bypassGateway": "bypass_gateway",
    "configId": "config_id",
    "enableThinking": "enable_thinking",
    "keepThinkingOutput": "keep_thinking_output",
    "thinkingBudget": "thinking_budget",
    "reasoningEffort": "reasoning_effort",
    "textVerbosity": "text_verbosity",
    "structuredOutputs": "structured_outputs",
    "parallelToolCalls": "parallel_tool_calls",
    "logitBias": "logit_bias",
    "strictJsonSchema": "strict_json_schema",
    "maxCompletionTokens": "max_completion_tokens",
    "serviceTier": "service_tier",
    "promptCacheKey": "prompt_cache_key",
    "promptCacheRetention": "prompt_cache_retention",
    "gpuPath": "gpu_path",
    "maxItems": "max_items",
}


def _verify_path_containment(resolved_path: Path, base_dir: Path) -> None:
    """Verify that a resolved file path stays within an allowed base directory."""
    normalized_base = base_dir.resolve()
    normalized_path = resolved_path.resolve()

    try:
        real_base = normalized_base.resolve()
    except OSError, RuntimeError:
        real_base = normalized_base

    try:
        real_path = normalized_path.resolve()
    except OSError, RuntimeError:
        real_path = normalized_path

    try:
        real_path.relative_to(real_base)
    except ValueError:
        raise SecurityError(
            f"Path traversal detected: {resolved_path} escapes base directory {base_dir}"
        ) from None


def _normalize_config_keys(value: Any) -> Any:
    """Normalize known MDA camelCase keys to the public Python snake_case shape."""
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = raw_key if isinstance(raw_key, str) else str(raw_key)
            normalized_key = _CAMEL_TO_SNAKE.get(key, key)
            normalized[normalized_key] = _normalize_config_keys(item)
        return normalized

    if isinstance(value, list):
        return [_normalize_config_keys(item) for item in value]

    return value


def _normalize_config_shape(config: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize projected MDA layouts into the public Python config shape.

    MDA stores provider/model inside ``metadata.snoai-llmix.common``. The public
    Python loader lifts those fields to the top level and removes them from
    ``common`` so the returned config matches the existing runtime contract.
    """
    normalized = cast(dict[str, Any], _normalize_config_keys(config))
    common_value = normalized.get("common")
    if isinstance(common_value, dict):
        common = dict(common_value)
        provider = common.pop("provider", None)
        model = common.pop("model", None)

        if provider is not None and "provider" not in normalized:
            normalized["provider"] = provider
        if model is not None and "model" not in normalized:
            normalized["model"] = model

        if common:
            normalized["common"] = common
        else:
            normalized.pop("common", None)

    return normalized


@dataclass(frozen=True)
class MdaConfigLoadOptions:
    """Options passed through to the MDA source loader."""

    verify_integrity: bool = False
    verify_signatures: bool = False
    enforce_requires: bool = False
    allowed_networks: list[str] | None = None
    trust_policy: Any | None = None
    rekor_client: Any | None = None
    sigstore_verifier: Any | None = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)


class _MetadataModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True, strict=True)


class _CommonParamsSchema(_StrictModel):
    max_output_tokens: int | None = Field(default=None, alias="maxOutputTokens", gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, alias="topP", ge=0, le=1)
    top_k: int | None = Field(default=None, alias="topK", gt=0)
    presence_penalty: float | None = Field(default=None, alias="presencePenalty")
    frequency_penalty: float | None = Field(default=None, alias="frequencyPenalty")
    stop_sequences: list[str] | None = Field(default=None, alias="stopSequences")
    seed: int | None = None
    max_retries: int | None = Field(default=None, alias="maxRetries", ge=0)
    enable_thinking: bool | None = Field(default=None, alias="enableThinking")
    keep_thinking_output: bool | None = Field(default=None, alias="keepThinkingOutput")


class _LLMixMdaCommonSchema(_CommonParamsSchema):
    provider: Literal[
        "openai",
        "anthropic",
        "google",
        "deepseek",
        "openrouter",
        "deepinfra",
        "together",
        "novita",
        "sno-gpu",
    ]
    model: str = Field(min_length=1)


class _OpenAIProviderOptionsSchema(_StrictModel):
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = (
        Field(default=None, alias="reasoningEffort")
    )
    parallel_tool_calls: bool | None = Field(default=None, alias="parallelToolCalls")
    user: str | None = None
    logprobs: bool | int | None = None
    logit_bias: dict[str, float] | None = Field(default=None, alias="logitBias")
    structured_outputs: bool | None = Field(default=None, alias="structuredOutputs")
    strict_json_schema: bool | None = Field(default=None, alias="strictJsonSchema")
    max_completion_tokens: int | None = Field(
        default=None, alias="maxCompletionTokens", gt=0
    )
    store: bool | None = None
    metadata: dict[str, str] | None = None
    prediction: dict[str, Any] | None = None
    service_tier: Literal["auto", "flex", "priority", "default"] | None = Field(
        default=None, alias="serviceTier"
    )
    text_verbosity: Literal["low", "medium", "high"] | None = Field(
        default=None, alias="textVerbosity"
    )
    prompt_cache_key: str | None = Field(default=None, alias="promptCacheKey")
    prompt_cache_retention: Literal["in_memory", "24h"] | None = Field(
        default=None, alias="promptCacheRetention"
    )
    safety_identifier: str | None = Field(default=None, alias="safetyIdentifier")


class _AnthropicThinkingConfigSchema(_StrictModel):
    type: Literal["enabled", "disabled"]
    budget_tokens: int | None = Field(default=None, alias="budgetTokens", gt=0)


class _AnthropicCacheControlSchema(_StrictModel):
    type: Literal["ephemeral"]
    ttl: str | None = None


class _AnthropicProviderOptionsSchema(_StrictModel):
    thinking: _AnthropicThinkingConfigSchema | None = None
    cache_control: _AnthropicCacheControlSchema | None = Field(
        default=None, alias="cacheControl"
    )
    disable_parallel_tool_use: bool | None = Field(
        default=None, alias="disableParallelToolUse"
    )
    send_reasoning: bool | None = Field(default=None, alias="sendReasoning")
    effort: Literal["high", "medium", "low"] | None = None
    tool_streaming: bool | None = Field(default=None, alias="toolStreaming")
    structured_output_mode: Literal["outputFormat", "jsonTool", "auto"] | None = Field(
        default=None, alias="structuredOutputMode"
    )


class _GoogleThinkingConfigSchema(_StrictModel):
    thinking_level: Literal["low", "high"] | None = Field(
        default=None, alias="thinkingLevel"
    )
    thinking_budget: int | None = Field(default=None, alias="thinkingBudget", gt=0)
    include_thoughts: bool | None = Field(default=None, alias="includeThoughts")


class _GoogleSafetySettingSchema(_StrictModel):
    category: str
    threshold: str


class _GoogleProviderOptionsSchema(_StrictModel):
    thinking_config: _GoogleThinkingConfigSchema | None = Field(
        default=None, alias="thinkingConfig"
    )
    cached_content: str | None = Field(default=None, alias="cachedContent")
    structured_outputs: bool | None = Field(default=None, alias="structuredOutputs")
    safety_settings: list[_GoogleSafetySettingSchema] | None = Field(
        default=None, alias="safetySettings"
    )
    response_modalities: list[str] | None = Field(
        default=None, alias="responseModalities"
    )


class _DeepSeekThinkingConfigSchema(_StrictModel):
    type: Literal["enabled", "disabled"]


class _DeepSeekProviderOptionsSchema(_StrictModel):
    thinking: _DeepSeekThinkingConfigSchema | None = None


class _OpenRouterProviderOptionsSchema(_StrictModel):
    provider: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None


class _SnoGpuProviderOptionsSchema(_StrictModel):
    enable_thinking: bool | None = Field(default=None, alias="enableThinking")
    thinking_budget: int | None = Field(default=None, alias="thinkingBudget", gt=0)
    gpu_path: str | None = Field(default=None, alias="gpuPath")


class _ProviderOptionsSchema(_StrictModel):
    openai: _OpenAIProviderOptionsSchema | None = None
    anthropic: _AnthropicProviderOptionsSchema | None = None
    google: _GoogleProviderOptionsSchema | None = None
    deepseek: _DeepSeekProviderOptionsSchema | None = None
    openrouter: _OpenRouterProviderOptionsSchema | None = None
    sno_gpu: _SnoGpuProviderOptionsSchema | None = Field(default=None, alias="sno-gpu")
    deepinfra: dict[str, Any] | None = None
    novita: dict[str, Any] | None = None
    together: dict[str, Any] | None = None


class _TimeoutConfigSchema(_StrictModel):
    total_time: float | None = Field(default=None, alias="totalTime", gt=0)
    stream_first_chunk_time: float | None = Field(
        default=None, alias="streamFirstChunkTime", gt=0
    )


class _CachingConfigSchema(_StrictModel):
    strategy: Literal[
        "native", "gateway", "disabled", "redis", "redis-or-memory", "memory"
    ]
    key: str | None = None
    ttl: int | None = Field(default=None, gt=0)
    max_items: int | None = Field(default=None, alias="maxItems", gt=0)


class _LLMixMdaNamespaceSchema(_StrictModel):
    common: _LLMixMdaCommonSchema
    provider_options: _ProviderOptionsSchema | None = Field(
        default=None, alias="providerOptions"
    )
    timeout: _TimeoutConfigSchema | None = None
    description: str | None = None
    deprecated: bool | None = None
    tags: list[str] | None = None
    caching: _CachingConfigSchema | None = None
    bypass_gateway: bool | None = Field(default=None, alias="bypassGateway")


class _MdaMetadataSchema(_MetadataModel):
    snoai_llmix: _LLMixMdaNamespaceSchema = Field(alias="snoai-llmix")


class _LLMixMdaPresetSchema(_StrictModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")
    metadata: _MdaMetadataSchema
    integrity: dict[str, Any] | None = None
    signatures: list[dict[str, Any]] | None = None
    doc_id: str | None = Field(default=None, alias="doc-id")
    title: str | None = None
    version: str | None = None
    requires: dict[str, Any] | None = None
    depends_on: list[dict[str, Any]] | None = Field(default=None, alias="depends-on")
    author: str | None = None
    tags: list[str] | None = None
    created_date: str | None = Field(default=None, alias="created-date")
    updated_date: str | None = Field(default=None, alias="updated-date")
    relationships: list[dict[str, Any]] | None = None


_VALID_PROVIDERS = {
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "openrouter",
    "deepinfra",
    "together",
    "novita",
    "sno-gpu",
}
_VALID_CACHE_STRATEGIES = {
    "native",
    "gateway",
    "disabled",
    "redis",
    "redis-or-memory",
    "memory",
}
_ANTHROPIC_MIN_BUDGET_TOKENS = 1024


def _dump_model(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude_none=True)


def _dump_provider_options(value: _ProviderOptionsSchema) -> dict[str, Any]:
    providers: tuple[tuple[str, str], ...] = (
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("google", "google"),
        ("deepseek", "deepseek"),
        ("openrouter", "openrouter"),
        ("sno_gpu", "sno-gpu"),
        ("deepinfra", "deepinfra"),
        ("novita", "novita"),
        ("together", "together"),
    )
    dumped: dict[str, Any] = {}
    for attr_name, provider_name in providers:
        provider_value = getattr(value, attr_name)
        if provider_value is None:
            continue
        if isinstance(provider_value, BaseModel):
            dumped[provider_name] = _dump_model(provider_value)
        else:
            dumped[provider_name] = _normalize_config_keys(provider_value)
    return dumped


def _reject_legacy_config_path(config_path: str) -> None:
    lower_path = config_path.lower()
    if lower_path.endswith(".yaml") or lower_path.endswith(".yml"):
        raise InvalidConfigError(
            f"Python LLMix presets use .mda files; YAML presets are no longer supported: {config_path}"
        )


def _ensure_mda_config_path(config_path: str) -> None:
    _reject_legacy_config_path(config_path)
    if not config_path.lower().endswith(".mda"):
        raise InvalidConfigError(
            f"Python LLMix presets must use .mda files: {config_path}"
        )


def _map_mda_load_error(exc: Exception, file_path: Path) -> Exception:
    if isinstance(exc, FileNotFoundError):
        return ConfigNotFoundError(f"Config file not found: {file_path}")
    if isinstance(exc, PermissionError):
        return ConfigAccessError(f"Permission denied reading config file: {file_path}")
    if isinstance(exc, MdaConfigError):
        return InvalidConfigError(f"MDA config failed for {file_path}: {exc}")
    return InvalidConfigError(f"MDA config failed for {file_path}: {exc}")


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidConfigError(f"{path} must be an object")
    return cast(dict[str, Any], value)


def _require_bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise InvalidConfigError(f"{path} must be a boolean")


def _require_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidConfigError(f"{path} must be an integer")
    return value


def _require_positive_int(value: Any, path: str) -> None:
    if _require_int(value, path) <= 0:
        raise InvalidConfigError(f"{path} must be a positive integer")


def _require_non_negative_int(value: Any, path: str) -> None:
    if _require_int(value, path) < 0:
        raise InvalidConfigError(f"{path} must be a non-negative integer")


def _require_finite_number(value: Any, path: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(float(value))
    ):
        raise InvalidConfigError(f"{path} must be a finite number")
    return float(value)


def _require_number_range(
    value: Any, path: str, minimum: float, maximum: float
) -> None:
    numeric_value = _require_finite_number(value, path)
    if numeric_value < minimum or numeric_value > maximum:
        raise InvalidConfigError(f"{path} must be between {minimum:g} and {maximum:g}")


def _require_positive_number(value: Any, path: str) -> None:
    if _require_finite_number(value, path) <= 0:
        raise InvalidConfigError(f"{path} must be a positive number")


def _require_literal(value: Any, path: str, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise InvalidConfigError(f"{path} must be one of: {allowed_text}")


def _validate_common(common: dict[str, Any], path: str) -> None:
    if "max_output_tokens" in common:
        _require_positive_int(common["max_output_tokens"], f"{path}.max_output_tokens")
    if "top_k" in common:
        _require_positive_int(common["top_k"], f"{path}.top_k")
    if "temperature" in common:
        _require_number_range(common["temperature"], f"{path}.temperature", 0, 2)
    if "top_p" in common:
        _require_number_range(common["top_p"], f"{path}.top_p", 0, 1)
    if "max_retries" in common:
        _require_non_negative_int(common["max_retries"], f"{path}.max_retries")


def _validate_caching(caching: dict[str, Any], path: str) -> None:
    _require_literal(
        caching.get("strategy"), f"{path}.strategy", _VALID_CACHE_STRATEGIES
    )
    if "ttl" in caching:
        _require_positive_int(caching["ttl"], f"{path}.ttl")
    if "max_items" in caching:
        _require_positive_int(caching["max_items"], f"{path}.max_items")


def _validate_timeout(timeout: dict[str, Any], path: str) -> None:
    if "total_time" in timeout:
        _require_positive_number(timeout["total_time"], f"{path}.total_time")
    if "stream_first_chunk_time" in timeout:
        _require_positive_number(
            timeout["stream_first_chunk_time"], f"{path}.stream_first_chunk_time"
        )


def _validate_provider_options(
    provider: str, provider_options: dict[str, Any], path: str
) -> None:
    if "openai" in provider_options:
        openai = _require_object(provider_options["openai"], f"{path}.openai")
        if "reasoning_effort" in openai:
            _require_literal(
                openai["reasoning_effort"],
                f"{path}.openai.reasoning_effort",
                {"minimal", "low", "medium", "high", "xhigh"},
            )
        if "max_completion_tokens" in openai:
            _require_positive_int(
                openai["max_completion_tokens"], f"{path}.openai.max_completion_tokens"
            )
        if "service_tier" in openai:
            _require_literal(
                openai["service_tier"],
                f"{path}.openai.service_tier",
                {"auto", "flex", "priority", "default"},
            )
        if "text_verbosity" in openai:
            _require_literal(
                openai["text_verbosity"],
                f"{path}.openai.text_verbosity",
                {"low", "medium", "high"},
            )

    if "anthropic" in provider_options:
        anthropic = _require_object(provider_options["anthropic"], f"{path}.anthropic")
        thinking = anthropic.get("thinking")
        if thinking is not None:
            thinking_obj = _require_object(thinking, f"{path}.anthropic.thinking")
            if "type" in thinking_obj:
                _require_literal(
                    thinking_obj["type"],
                    f"{path}.anthropic.thinking.type",
                    {"enabled", "disabled"},
                )
            if "budget_tokens" in thinking_obj:
                _require_positive_int(
                    thinking_obj["budget_tokens"],
                    f"{path}.anthropic.thinking.budget_tokens",
                )
                if (
                    provider == "anthropic"
                    and thinking_obj.get("type") == "enabled"
                    and thinking_obj["budget_tokens"] < _ANTHROPIC_MIN_BUDGET_TOKENS
                ):
                    raise InvalidConfigError(
                        f"{path}.anthropic.thinking.budget_tokens must be >= {_ANTHROPIC_MIN_BUDGET_TOKENS} when Anthropic thinking is enabled"
                    )

    if "google" in provider_options:
        google = _require_object(provider_options["google"], f"{path}.google")
        thinking = google.get("thinking_config")
        if thinking is not None:
            thinking_obj = _require_object(thinking, f"{path}.google.thinking_config")
            if "thinking_level" in thinking_obj:
                _require_literal(
                    thinking_obj["thinking_level"],
                    f"{path}.google.thinking_config.thinking_level",
                    {"low", "high"},
                )
            if "thinking_budget" in thinking_obj:
                _require_positive_int(
                    thinking_obj["thinking_budget"],
                    f"{path}.google.thinking_config.thinking_budget",
                )

    if "deepseek" in provider_options:
        deepseek = _require_object(provider_options["deepseek"], f"{path}.deepseek")
        thinking = deepseek.get("thinking")
        if thinking is not None:
            thinking_obj = _require_object(thinking, f"{path}.deepseek.thinking")
            if "type" in thinking_obj:
                _require_literal(
                    thinking_obj["type"],
                    f"{path}.deepseek.thinking.type",
                    {"enabled", "disabled"},
                )

    for provider_name in ("deepinfra", "novita", "sno-gpu"):
        if provider_name not in provider_options:
            continue
        provider_value = _require_object(
            provider_options[provider_name], f"{path}.{provider_name}"
        )
        if "enable_thinking" in provider_value:
            _require_bool(
                provider_value["enable_thinking"],
                f"{path}.{provider_name}.enable_thinking",
            )
        if "thinking_budget" in provider_value:
            _require_positive_int(
                provider_value["thinking_budget"],
                f"{path}.{provider_name}.thinking_budget",
            )


def _validate_runtime_config(value: dict[str, Any], source: str | Path) -> None:
    provider = value.get("provider")
    if not isinstance(provider, str) or provider not in _VALID_PROVIDERS:
        raise InvalidConfigError(f"Invalid or missing provider in {source}")
    model = value.get("model")
    if not isinstance(model, str) or not model:
        raise InvalidConfigError(f"Invalid or missing model in {source}")

    common = value.get("common")
    if common is not None:
        _validate_common(_require_object(common, "common"), "common")
    caching = value.get("caching")
    if caching is not None:
        _validate_caching(_require_object(caching, "caching"), "caching")
    timeout = value.get("timeout")
    if timeout is not None:
        _validate_timeout(_require_object(timeout, "timeout"), "timeout")
    provider_options = value.get("provider_options")
    if provider_options is not None:
        _validate_provider_options(
            provider,
            _require_object(provider_options, "provider_options"),
            "provider_options",
        )


def _project_mda_preset_to_config(
    preset: _LLMixMdaPresetSchema, source: Path
) -> LLMConfig:
    namespace = preset.metadata.snoai_llmix
    config = _normalize_config_shape({"common": _dump_model(namespace.common)})

    if namespace.provider_options is not None:
        provider_options = _dump_provider_options(namespace.provider_options)
        if provider_options:
            config["provider_options"] = provider_options
    if namespace.timeout is not None:
        config["timeout"] = _dump_model(namespace.timeout)
    if namespace.description is not None:
        config["description"] = namespace.description
    else:
        config["description"] = preset.description
    if namespace.deprecated is not None:
        config["deprecated"] = namespace.deprecated
    if namespace.tags is not None:
        config["tags"] = namespace.tags
    elif preset.tags is not None:
        config["tags"] = preset.tags
    if namespace.caching is not None:
        config["caching"] = _dump_model(namespace.caching)
    if namespace.bypass_gateway is not None:
        config["bypass_gateway"] = namespace.bypass_gateway

    _validate_runtime_config(config, source)
    return cast(LLMConfig, config)


def build_mda_config_file_path(
    config_dir: str | Path, module: str, preset: str
) -> Path:
    """Build the standard MDA config path for a module preset."""
    validate_module(module)
    validate_preset(preset)
    return Path(config_dir).expanduser().resolve() / module / f"{preset}.mda"


def load_mda_config(
    path: str | Path, options: MdaConfigLoadOptions | None = None
) -> LLMConfig:
    """Load an explicit LLMix MDA source file."""
    _ensure_mda_config_path(str(path))
    requested_path = Path(path).expanduser()
    _verify_path_containment(requested_path, requested_path.parent)
    file_path = requested_path.resolve()
    try:
        load_options = options or MdaConfigLoadOptions()
        preset = load_mda_source(
            file_path,
            schema=_LLMixMdaPresetSchema,
            verify_integrity=bool(load_options.verify_integrity),
            verify_signatures=bool(load_options.verify_signatures),
            enforce_requires=bool(load_options.enforce_requires),
            allowed_networks=load_options.allowed_networks,
            trust_policy=load_options.trust_policy,
            rekor_client=load_options.rekor_client,
            sigstore_verifier=load_options.sigstore_verifier,
        )
    except Exception as exc:
        mapped = _map_mda_load_error(exc, file_path)
        if isinstance(exc, MdaConfigError):
            raise mapped from exc
        raise mapped from None
    return _project_mda_preset_to_config(cast(_LLMixMdaPresetSchema, preset), file_path)


def load_mda_config_preset(
    name: str, base_dir: str | Path, options: MdaConfigLoadOptions | None = None
) -> LLMConfig:
    """
    Load a preset file from ``{base_dir}/{name}.mda``.

    ``name`` may be a bare preset (`"extraction"`) or include a `.mda` suffix.
    """
    _reject_legacy_config_path(name)
    preset_name = name[:-4] if name.lower().endswith(".mda") else name
    validate_preset(preset_name)

    presets_dir = Path(base_dir).expanduser().resolve()
    module_name = presets_dir.name
    validate_module(module_name)

    file_path = presets_dir / f"{preset_name}.mda"
    _verify_path_containment(file_path, presets_dir)
    return load_mda_config(file_path, options)


def load_mda_config_from_file(
    config_dir: str | Path,
    module: str,
    preset: str,
    options: MdaConfigLoadOptions | None = None,
) -> LLMConfig:
    """Load a module preset from the standard MDA config directory layout."""
    file_path = build_mda_config_file_path(config_dir, module, preset)
    _verify_path_containment(file_path, Path(config_dir).expanduser().resolve())
    return load_mda_config(file_path, options)
