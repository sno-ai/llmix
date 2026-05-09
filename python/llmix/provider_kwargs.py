"""Provider kwargs injection -- per-provider request mutation before dispatch.

Each provider can define a `transform_kwargs` callback that mutates request
parameters before the API call is made. This keeps provider-specific quirks
(reasoning model parameter stripping, default extra_body injection, etc.)
isolated from the main client code.

Provider-specific behavior is isolated here so the main client dispatch path
can stay provider-neutral.
"""

import re
from typing import Any, Protocol, TypedDict

from llmix.env import get_gpu_base_url

# =============================================================================
# Types
# =============================================================================


class TransformKwargsContext(TypedDict, total=False):
    """Context passed to transform_kwargs callbacks."""

    model: str
    provider: str
    messages: list[dict[str, Any]]
    temperature: float | None
    top_p: float | None
    enable_thinking: bool | None
    provider_options: dict[str, Any]
    base_url: str | None


class TransformKwargsCallback(Protocol):
    """Callable that mutates request kwargs based on provider-specific rules."""

    def __call__(self, ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]: ...


# =============================================================================
# Core dispatch
# =============================================================================


def apply_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any], callback: TransformKwargsCallback | None) -> dict[str, Any]:
    """Apply a provider's transform_kwargs callback if non-null.

    Returns kwargs unchanged when callback is None.
    """
    if callback is None:
        return kwargs
    return callback(ctx, kwargs)


# =============================================================================
# OpenAI: strip temperature / top_p for reasoning models
# =============================================================================


def _is_reasoning_model(model_id: str) -> bool:
    """Check if model is an OpenAI reasoning model.

    Mirrors logic from model_capabilities._is_reasoning_model without
    pulling in the full types.py import chain.
    # TEMP: regex patch — migrate to config-driven model capabilities (see model-capabilities.json)
    """
    lower = model_id.lower()
    return bool(re.match(r"^o\d", lower)) or lower.startswith(("gpt-5", "codex-", "computer-use"))


def openai_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Strip temperature and top_p for OpenAI reasoning models.

    Reasoning models (o-series, gpt-5*, codex-, computer-use-)
    require temperature=1 and do not accept top_p.
    """
    model = ctx.get("model", "")
    if not _is_reasoning_model(model):
        return kwargs

    kwargs = dict(kwargs)
    kwargs.pop("temperature", None)
    kwargs.pop("top_p", None)

    # Reasoning models use max_completion_tokens, not max_tokens
    max_tokens = kwargs.pop("max_tokens", None)
    if max_tokens is not None and "max_completion_tokens" not in kwargs:
        kwargs["max_completion_tokens"] = max_tokens

    return kwargs


# =============================================================================
# OpenRouter: inject extra_body.provider.sort = "price"
# =============================================================================

_OPENROUTER_DEFAULT_PROVIDER = {"sort": "price"}


def openrouter_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Inject default provider sorting config for OpenRouter.

    Configured OpenRouter provider/reasoning options are copied into
    extra_body when the caller did not already provide explicit kwargs.
    Falls back to extra_body.provider.sort = "price".
    """
    kwargs = dict(kwargs)
    provider_options = ctx.get("provider_options") or {}
    openrouter_options = provider_options.get("openrouter") or {}

    extra_body: dict[str, Any] = kwargs.get("extra_body") or {}
    if "provider" not in extra_body:
        configured_provider = openrouter_options.get("provider")
        provider = configured_provider if isinstance(configured_provider, dict) else _OPENROUTER_DEFAULT_PROVIDER
        extra_body = {**extra_body, "provider": dict(provider)}
    if "reasoning" not in extra_body and isinstance(openrouter_options.get("reasoning"), dict):
        extra_body = {**extra_body, "reasoning": dict(openrouter_options["reasoning"])}
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


# =============================================================================
# Gemini: default thinking_budget=0, override from providerOptions
# =============================================================================


def gemini_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Set default ThinkingConfig(thinking_budget=0), override from providerOptions.

    Disables thinking by default. If providerOptions.google.thinking_config.
    thinking_budget is set, uses that value instead. The legacy flat
    providerOptions.google.thinking_budget key is also accepted for
    compatibility.
    """
    kwargs = dict(kwargs)

    provider_options = ctx.get("provider_options") or {}
    google_opts = provider_options.get("google") or {}
    raw_thinking_config = google_opts.get("thinking_config") or {}
    thinking_budget = raw_thinking_config.get("thinking_budget")
    if thinking_budget is None:
        thinking_budget = google_opts.get("thinking_budget")

    thinking_config = kwargs.get("thinking_config") or {}
    # Only set default if not already explicitly configured
    if "thinking_budget" not in thinking_config:
        if thinking_budget is None and ctx.get("enable_thinking") is True:
            return kwargs
        if thinking_budget is None:
            thinking_budget = 0
        thinking_config = {**thinking_config, "thinking_budget": thinking_budget}
        kwargs["thinking_config"] = thinking_config

    return kwargs


# =============================================================================
# Sno GPU: construct base URL from providerOptions["sno-gpu"].gpuPath
# =============================================================================


def sno_gpu_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Construct base URL from providerOptions["sno-gpu"].gpuPath.

    Builds: {base_url}/{gpuPath}/v1 when gpuPath is present.
    Falls back to {base_url}/v1 when gpuPath is absent.

    Also propagates ``enable_thinking`` (from providerOptions["sno-gpu"] or
    common) into ``extra_body`` in BOTH the top-level form (the wrapper
    translates this to ``chat_template_kwargs.enable_thinking``) AND directly
    under ``chat_template_kwargs`` (so direct-vLLM endpoints honor it without
    the wrapper). Without this, the MDA preset's ``enableThinking: false`` is
    silently dropped and the reasoning model burns the whole token budget on
    chain-of-thought, returning empty ``content``.
    """
    kwargs = dict(kwargs)

    provider_options = ctx.get("provider_options") or {}
    sno_gpu_opts = provider_options.get("sno-gpu") or {}
    gpu_path: str | None = sno_gpu_opts.get("gpu_path")

    enable_thinking_opt = sno_gpu_opts.get("enable_thinking")
    common_thinking = ctx.get("enable_thinking")
    if enable_thinking_opt is not None or common_thinking is not None:
        flag = bool(enable_thinking_opt) if enable_thinking_opt is not None else bool(common_thinking)
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body.setdefault("enable_thinking", flag)
        chat_template_kwargs = dict(extra_body.get("chat_template_kwargs") or {})
        chat_template_kwargs.setdefault("enable_thinking", flag)
        extra_body["chat_template_kwargs"] = chat_template_kwargs
        kwargs["extra_body"] = extra_body

    base_url = ctx.get("base_url") or ""
    base = base_url.rstrip("/")
    if not base:
        base = get_gpu_base_url().rstrip("/")
    # Strip trailing /v1 if present so we can reconstruct cleanly
    if base.endswith("/v1"):
        base = base[:-3]

    if not base.strip():
        raise ValueError("sno-gpu provider requires a non-empty base_url in config or GPU_BASE_URL env var")

    if gpu_path:
        if '..' in gpu_path or not re.match(r'^[a-zA-Z0-9_/-]+$', gpu_path):
            raise ValueError(f"Invalid gpu_path: {gpu_path!r}")
        kwargs["base_url"] = f"{base}/{gpu_path}/v1"
    else:
        kwargs["base_url"] = f"{base}/v1"

    return kwargs


# =============================================================================
# Registry: provider name -> default callback
# =============================================================================

PROVIDER_KWARGS_REGISTRY: dict[str, TransformKwargsCallback] = {
    "openai": openai_transform_kwargs,
    "openrouter": openrouter_transform_kwargs,
    # Legacy DeepSeek provider configs route through OpenRouter.
    "deepseek": openrouter_transform_kwargs,
    "google": gemini_transform_kwargs,
    "gemini": gemini_transform_kwargs,
    "sno-gpu": sno_gpu_transform_kwargs,
}
