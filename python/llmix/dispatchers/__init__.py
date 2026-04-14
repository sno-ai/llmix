"""Thin provider dispatchers for the CallPipeline public API."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from llmix.env import (
    get_anthropic_api_key,
    get_deepinfra_api_key,
    get_gemini_api_key,
    get_novita_api_key,
    get_openai_api_key,
    get_openrouter_api_key,
    get_sno_llm_api_key,
    get_together_api_key,
)
from llmix.pipeline import DispatchInput, LLMUsage, ProviderDispatchFn, ProviderError, ProviderResult
from llmix.provider_urls import (
    ANTHROPIC_BASE_URL,
    DEEPINFRA_BASE_URL,
    GOOGLE_BASE_URL,
    NOVITA_BASE_URL,
    OPENAI_BASE_URL,
    OPENROUTER_BASE_URL,
    TOGETHER_BASE_URL,
)
from llmix.providers.base import LLMResponse
from llmix.providers.onprem_gpu_client import build_gpu_base_url

if TYPE_CHECKING:
    from llmix.providers import AsyncAnthropicClient, AsyncGeminiClient, AsyncOpenAIClient, DeepInfraClient, NovitaClient, SnoGpuClient, TogetherClient

__all__ = [
    "anthropic_dispatch",
    "deepinfra_dispatch",
    "gemini_dispatch",
    "novita_dispatch",
    "openai_dispatch",
    "openrouter_dispatch",
    "sno_gpu_dispatch",
    "together_dispatch",
]

_DEEPSEEK_MODEL_MAPPINGS: dict[str, str] = {
    "deepseek-chat": "deepseek/deepseek-chat-v3-0324",
    "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
    "deepseek-v3.2-speciale": "deepseek/deepseek-chat-v3-0324:free",
    "deepseek-reasoner": "deepseek/deepseek-reasoner",
}


def _load_provider_attr(name: str) -> Any:
    providers_module = importlib.import_module("llmix.providers")
    return getattr(providers_module, name)


def _require_api_key(api_key: str | None, env_value: str | None, *, env_name: str, provider: str) -> str:
    resolved = api_key or env_value
    if resolved and resolved.strip():
        return resolved
    raise ProviderError(f"{provider} provider requires {env_name}")


BYPASS_KEY_POOL_ATTR = "__llmix_bypass_key_pool_providers__"


def _mark_bypass(dispatch: ProviderDispatchFn, client: object | None, provider: str) -> ProviderDispatchFn:
    """Tag a dispatch function so the pipeline rejects KeyPool registration
    for providers whose dispatch was built with a prebuilt ``client=``.

    A prebuilt client's baked-in ``api_key`` authenticates every request, but
    the pipeline would still treat ``ctx.api_key`` (from ``KeyPool.select``)
    as live — so a 401/403 would call ``KeyPool.mark_dead(ctx.api_key)`` and
    silently kill an unrelated pool key. Hard-gating ``set_key_pool`` for
    these providers eliminates that corruption path.
    """
    if client is None:
        return dispatch
    existing: frozenset[str] = getattr(dispatch, BYPASS_KEY_POOL_ATTR, frozenset())
    try:
        setattr(dispatch, BYPASS_KEY_POOL_ATTR, existing | frozenset({provider}))
    except (AttributeError, TypeError):
        # Some callables (e.g. builtins) don't accept arbitrary attrs. The
        # factories below always return fresh closures, so this path is
        # defensive only.
        pass
    return dispatch


def _coerce_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Mapping):
            normalized.append(dict(message))
    return normalized


def _split_system_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    provider_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                instructions.append(content)
            continue
        provider_messages.append(message)
    joined = "\n\n".join(instructions) if instructions else None
    return joined, provider_messages


def _resolve_text(kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    text = kwargs.get("text")
    if isinstance(text, Mapping):
        return dict(text)

    response_format = kwargs.get("response_format")
    if isinstance(response_format, str):
        return {"format": response_format}
    if isinstance(response_format, Mapping):
        format_value = response_format.get("type")
        if format_value is not None:
            return {"format": format_value}
    return None


def _resolve_stop(kwargs: Mapping[str, Any]) -> str | list[str] | None:
    stop = kwargs.get("stop")
    if isinstance(stop, str):
        return stop
    if isinstance(stop, list) and all(isinstance(item, str) for item in stop):
        return cast("list[str]", stop)

    stop_sequences = kwargs.get("stop_sequences")
    if isinstance(stop_sequences, str):
        return stop_sequences
    if isinstance(stop_sequences, list) and all(isinstance(item, str) for item in stop_sequences):
        return cast("list[str]", stop_sequences)
    return None


def _resolve_max_output_tokens(kwargs: Mapping[str, Any]) -> int | None:
    for key in ("max_output_tokens", "max_completion_tokens", "max_tokens"):
        value = kwargs.get(key)
        if isinstance(value, int):
            return value
    return None


def _normalize_usage(usage: Mapping[str, Any]) -> LLMUsage:
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or (input_tokens + output_tokens))
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


def _normalize_response(response: LLMResponse, fallback_model: str) -> ProviderResult:
    if not response.success:
        raise ProviderError(response.error or "Provider request failed")
    return ProviderResult(
        content=response.content,
        model=response.model or fallback_model,
        usage=_normalize_usage(response.usage),
        tool_calls=cast("list[dict[str, Any]] | None", response.tool_calls),
    )


def _resolve_base_url(ctx: DispatchInput, default: str) -> str:
    for source in (
        ctx.kwargs.get("base_url"),
        ctx.config.get("base_url"),
        ctx.config.get("baseUrl"),
        default,
    ):
        if isinstance(source, str) and source.strip():
            return source
    return default


def _resolve_gpu_base_url(ctx: DispatchInput) -> str:
    explicit = ctx.kwargs.get("base_url")
    if isinstance(explicit, str) and explicit.strip():
        return explicit

    config_base_url = ctx.config.get("base_url") or ctx.config.get("baseUrl")
    if isinstance(config_base_url, str) and config_base_url.strip():
        return config_base_url

    sno_gpu_options = _resolve_provider_options(ctx, "sno-gpu")
    gpu_path = sno_gpu_options.get("gpu_path")
    if isinstance(gpu_path, str) and gpu_path.strip():
        return build_gpu_base_url(gpu_path)
    return build_gpu_base_url()


def _resolve_provider_options(ctx: DispatchInput, provider: str) -> dict[str, Any]:
    provider_options = ctx.config.get("provider_options")
    if not isinstance(provider_options, Mapping):
        return {}
    options = provider_options.get(provider)
    if not isinstance(options, Mapping):
        return {}
    return dict(options)


def _resolve_common(ctx: DispatchInput) -> dict[str, Any]:
    common = ctx.config.get("common")
    if not isinstance(common, Mapping):
        return {}
    return dict(common)


def _resolve_boolean_option(option_value: Any, fallback_value: Any, *, default: bool = False) -> bool:
    if option_value is not None:
        return bool(option_value)
    if fallback_value is not None:
        return bool(fallback_value)
    return default


def _build_openai_kwargs(ctx: DispatchInput) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if "temperature" in ctx.kwargs:
        kwargs["temperature"] = ctx.kwargs["temperature"]
    max_output_tokens = _resolve_max_output_tokens(ctx.kwargs)
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens

    text = _resolve_text(ctx.kwargs)
    if text is not None:
        kwargs["text"] = text

    for key in ("tools", "tool_choice", "parallel_tool_calls", "allowed_tools", "store", "reasoning_effort"):
        if key in ctx.kwargs:
            kwargs[key] = ctx.kwargs[key]
    return kwargs


def _build_chat_provider_kwargs(ctx: DispatchInput) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in (
        "temperature",
        "top_p",
        "seed",
        "response_format",
        "presence_penalty",
        "frequency_penalty",
        "top_k",
    ):
        value = ctx.kwargs.get(key)
        if value is not None:
            kwargs[key] = value

    max_output_tokens = _resolve_max_output_tokens(ctx.kwargs)
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens

    text = _resolve_text(ctx.kwargs)
    if text is not None:
        kwargs["text"] = text

    stop = _resolve_stop(ctx.kwargs)
    if stop is not None:
        kwargs["stop"] = stop

    for key in ("tools", "tool_choice", "parallel_tool_calls", "allowed_tools", "store", "reasoning_effort"):
        value = ctx.kwargs.get(key)
        if value is not None:
            kwargs[key] = value
    return kwargs


def _map_deepseek_model(model: str) -> str:
    if model.startswith("deepseek/"):
        return model
    return _DEEPSEEK_MODEL_MAPPINGS.get(model, f"deepseek/{model}")


def openai_dispatch(client: AsyncOpenAIClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        openai_client_cls = cast("type[AsyncOpenAIClient]", _load_provider_attr("AsyncOpenAIClient"))
        resolved_client = client or openai_client_cls(
            api_key=_require_api_key(ctx.api_key, get_openai_api_key(), env_name="OPENAI_API_KEY", provider="openai"),
            base_url=_resolve_base_url(ctx, OPENAI_BASE_URL),
            model=ctx.model,
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            **_build_openai_kwargs(ctx),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "openai")


def anthropic_dispatch(client: AsyncAnthropicClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        anthropic_client_cls = cast("type[AsyncAnthropicClient]", _load_provider_attr("AsyncAnthropicClient"))
        api_key = _require_api_key(ctx.api_key, get_anthropic_api_key(), env_name="ANTHROPIC_API_KEY", provider="anthropic")
        resolved_client = client or anthropic_client_cls(api_key=api_key, base_url=_resolve_base_url(ctx, ANTHROPIC_BASE_URL), model=ctx.model)
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            temperature=cast("float | None", ctx.kwargs.get("temperature")),
            max_output_tokens=_resolve_max_output_tokens(ctx.kwargs),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "anthropic")


def gemini_dispatch(client: AsyncGeminiClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        gemini_client_cls = cast("type[AsyncGeminiClient]", _load_provider_attr("AsyncGeminiClient"))
        resolved_client = client or gemini_client_cls(
            api_key=_require_api_key(ctx.api_key, get_gemini_api_key(), env_name="GEMINI_API_KEY", provider="google"),
            base_url=_resolve_base_url(ctx, GOOGLE_BASE_URL),
            model=ctx.model,
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            temperature=cast("float | None", ctx.kwargs.get("temperature")),
            max_output_tokens=_resolve_max_output_tokens(ctx.kwargs),
            text=_resolve_text(ctx.kwargs),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "gemini")


def deepinfra_dispatch(client: DeepInfraClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        deepinfra_options = _resolve_provider_options(ctx, "deepinfra")
        common = _resolve_common(ctx)
        deepinfra_client_cls = cast("type[DeepInfraClient]", _load_provider_attr("DeepInfraClient"))
        resolved_client = client or deepinfra_client_cls(
            api_key=_require_api_key(ctx.api_key, get_deepinfra_api_key(), env_name="DEEPINFRA_API_KEY", provider="deepinfra"),
            base_url=_resolve_base_url(ctx, DEEPINFRA_BASE_URL),
            model=ctx.model,
            enable_thinking=_resolve_boolean_option(
                deepinfra_options.get("enable_thinking"),
                common.get("enable_thinking"),
                default=False,
            ),
            thinking_budget=cast("int | None", deepinfra_options.get("thinking_budget")),
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            **_build_chat_provider_kwargs(ctx),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "deepinfra")


def openrouter_dispatch(client: AsyncOpenAIClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        resolved_model = _map_deepseek_model(ctx.model)
        openai_client_cls = cast("type[AsyncOpenAIClient]", _load_provider_attr("AsyncOpenAIClient"))
        resolved_client = client or openai_client_cls(
            api_key=_require_api_key(ctx.api_key, get_openrouter_api_key(), env_name="OPENROUTER_API_KEY", provider="openrouter"),
            base_url=_resolve_base_url(ctx, OPENROUTER_BASE_URL),
            model=resolved_model,
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=resolved_model,
            instructions=instructions,
            **_build_openai_kwargs(ctx),
        )
        return _normalize_response(response, resolved_model)

    return _mark_bypass(dispatch, client, "openrouter")


def novita_dispatch(client: NovitaClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        novita_options = _resolve_provider_options(ctx, "novita")
        common = _resolve_common(ctx)
        novita_client_cls = cast("type[NovitaClient]", _load_provider_attr("NovitaClient"))
        resolved_client = client or novita_client_cls(
            api_key=_require_api_key(ctx.api_key, get_novita_api_key(), env_name="NOVITA_API_KEY", provider="novita"),
            base_url=_resolve_base_url(ctx, NOVITA_BASE_URL),
            model=ctx.model,
            enable_thinking=_resolve_boolean_option(
                novita_options.get("enable_thinking"),
                common.get("enable_thinking"),
                default=False,
            ),
            thinking_budget=cast("int | None", novita_options.get("thinking_budget")),
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            **_build_chat_provider_kwargs(ctx),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "novita")


def sno_gpu_dispatch(client: SnoGpuClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        sno_gpu_options = _resolve_provider_options(ctx, "sno-gpu")
        common = _resolve_common(ctx)
        sno_gpu_client_cls = cast("type[SnoGpuClient]", _load_provider_attr("SnoGpuClient"))
        resolved_client = client or sno_gpu_client_cls(
            service_secret=_require_api_key(ctx.api_key, get_sno_llm_api_key(), env_name="SNO_LLM_API_KEY", provider="sno-gpu"),
            model=ctx.model,
            base_url=_resolve_gpu_base_url(ctx),
            enable_thinking=_resolve_boolean_option(
                sno_gpu_options.get("enable_thinking"),
                common.get("enable_thinking"),
                default=False,
            ),
            thinking_budget=cast("int | None", sno_gpu_options.get("thinking_budget")),
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            **_build_chat_provider_kwargs(ctx),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "sno-gpu")


def together_dispatch(client: TogetherClient | None = None) -> ProviderDispatchFn:
    async def dispatch(ctx: DispatchInput) -> ProviderResult:
        together_client_cls = cast("type[TogetherClient]", _load_provider_attr("TogetherClient"))
        resolved_client = client or together_client_cls(
            api_key=_require_api_key(ctx.api_key, get_together_api_key(), env_name="TOGETHER_API_KEY", provider="together"),
            base_url=_resolve_base_url(ctx, TOGETHER_BASE_URL),
            model=ctx.model,
        )
        instructions, provider_messages = _split_system_messages(_coerce_messages(ctx.messages))
        response = await resolved_client.response_completion(
            input=provider_messages,
            model=ctx.model,
            instructions=instructions,
            **_build_chat_provider_kwargs(ctx),
        )
        return _normalize_response(response, ctx.model)

    return _mark_bypass(dispatch, client, "together")
