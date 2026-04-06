"""Provider kwargs injection -- per-provider request mutation before dispatch.

Each provider can define a `transform_kwargs` callback that mutates request
parameters before the API call is made. This keeps provider-specific quirks
(reasoning model parameter stripping, default extra_body injection, etc.)
isolated from the main client code.

Ported from repo-reference/llm-provider/src/llm_provider/providers/_registry.py
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

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
    provider_options: dict[str, Any]
    base_url: str | None


class TransformKwargsCallback(Protocol):
    """Callable that mutates request kwargs based on provider-specific rules."""

    def __call__(self, ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]: ...


# =============================================================================
# Core dispatch
# =============================================================================


def apply_transform_kwargs(
    ctx: TransformKwargsContext,
    kwargs: dict[str, Any],
    callback: TransformKwargsCallback | None,
) -> dict[str, Any]:
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
    pulling in the types.py import chain (which requires lib.infra).
    """
    lower = model_id.lower()
    if lower.startswith("gpt-5-chat"):
        return False
    return lower.startswith(("o", "gpt-5", "codex-", "computer-use"))


def openai_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Strip temperature and top_p for OpenAI reasoning models.

    Reasoning models (o-series, gpt-5 except gpt-5-chat, codex-, computer-use-)
    require temperature=1 and do not accept top_p.
    """
    model = ctx.get("model", "")
    if not _is_reasoning_model(model):
        return kwargs

    kwargs = dict(kwargs)
    kwargs.pop("temperature", None)
    kwargs.pop("top_p", None)
    return kwargs


# =============================================================================
# OpenRouter (DeepSeek): inject extra_body.provider.sort = "price"
# =============================================================================

_OPENROUTER_DEFAULT_PROVIDER = {"sort": "price"}


def openrouter_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """Inject default provider sorting config for OpenRouter.

    Sets extra_body.provider.sort = "price" when not already present.
    """
    kwargs = dict(kwargs)
    extra_body: dict[str, Any] = kwargs.get("extra_body") or {}
    if "provider" not in extra_body:
        extra_body = {**extra_body, "provider": {**_OPENROUTER_DEFAULT_PROVIDER}}
        kwargs["extra_body"] = extra_body
    return kwargs


# =============================================================================
# Gemini: default thinking_budget=0, override from providerOptions
# =============================================================================


def gemini_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Set default ThinkingConfig(thinking_budget=0), override from providerOptions.

    Disables thinking by default. If providerOptions.google.thinkingBudget is
    set, uses that value instead.
    """
    kwargs = dict(kwargs)

    provider_options = ctx.get("provider_options") or {}
    google_opts = provider_options.get("google") or {}
    thinking_budget: int = google_opts.get("thinking_budget", 0)

    thinking_config = kwargs.get("thinking_config") or {}
    # Only set default if not already explicitly configured
    if "thinking_budget" not in thinking_config:
        thinking_config = {**thinking_config, "thinking_budget": thinking_budget}
        kwargs["thinking_config"] = thinking_config

    return kwargs


# =============================================================================
# Sno GPU: construct base URL from providerOptions.snogpu.gpuPath
# =============================================================================


def snogpu_transform_kwargs(ctx: TransformKwargsContext, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Construct base URL from providerOptions.snogpu.gpuPath.

    Builds: {base_url}/{gpuPath}/v1 when gpuPath is present.
    Falls back to {base_url}/v1 when gpuPath is absent.
    """
    kwargs = dict(kwargs)

    provider_options = ctx.get("provider_options") or {}
    snogpu_opts = provider_options.get("snogpu") or {}
    gpu_path: str | None = snogpu_opts.get("gpu_path")

    base_url = ctx.get("base_url") or ""
    # Strip trailing /v1 if present so we can reconstruct cleanly
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]

    if not base.strip():
        raise ValueError(
            "snogpu provider requires a non-empty base_url in config"
        )

    if gpu_path:
        kwargs["base_url"] = f"{base}/{gpu_path}/v1"
    else:
        kwargs["base_url"] = f"{base}/v1"

    return kwargs


# =============================================================================
# Registry: provider name -> default callback
# =============================================================================

PROVIDER_KWARGS_REGISTRY: dict[str, TransformKwargsCallback] = {
    "openai": openai_transform_kwargs,
    "deepseek": openrouter_transform_kwargs,
    "google": gemini_transform_kwargs,
    "snogpu": snogpu_transform_kwargs,
}
