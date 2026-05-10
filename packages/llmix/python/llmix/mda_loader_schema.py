"""Pydantic schema and projection for LLMix MDA presets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from llmix.mda_loader_validation import _validate_runtime_config
from llmix.types import LLMConfig


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
