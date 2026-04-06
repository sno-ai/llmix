"""HTTP/2 transport configuration and factory functions.

Provides httpx client factories for HTTP/2-enabled providers (OpenAI, Gemini)
and HTTP/1.1 for proxy-based providers (OpenRouter, Helicone).

Gemini note: google-genai uses httpx internally, so HTTP/2 comes for free
when httpx[http2] is installed (already in pyproject.toml deps). No explicit
client injection is needed for Gemini -- the SDK handles it.

Reference: repo-reference/llm-provider/src/llm_provider/providers/_registry.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class HeaderFeedbackReceiver(Protocol):
    """Anything with on_header_feedback(remaining, limit)."""

    def on_header_feedback(self, remaining: int, limit: int) -> None: ...


# ---------------------------------------------------------------------------
# Provider transport registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderTransportConfig:
    """Declares the HTTP/2 preference for a provider."""

    name: str
    http2: bool = False


PROVIDER_TRANSPORT: dict[str, ProviderTransportConfig] = {
    "openai": ProviderTransportConfig(name="openai", http2=True),
    "anthropic": ProviderTransportConfig(name="anthropic", http2=False),
    "google": ProviderTransportConfig(name="google", http2=True),
    "deepseek": ProviderTransportConfig(name="deepseek", http2=False),
    "openrouter": ProviderTransportConfig(name="openrouter", http2=False),
    "helicone": ProviderTransportConfig(name="helicone", http2=False),
}


def get_provider_transport(provider: str) -> ProviderTransportConfig:
    """Look up transport config for a provider. Returns http2=False for unknown providers."""
    return PROVIDER_TRANSPORT.get(
        provider,
        ProviderTransportConfig(name=provider, http2=False),
    )


# ---------------------------------------------------------------------------
# httpx client factories
# ---------------------------------------------------------------------------


def create_http2_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx AsyncClient with HTTP/2 enabled.

    Use for providers that benefit from HTTP/2 multiplexing (OpenAI direct).
    """
    import httpx as _httpx

    return _httpx.AsyncClient(http2=True, **kwargs)


def create_http1_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx AsyncClient with HTTP/1.1 only.

    Use for proxy-based providers (OpenRouter, Helicone) where HTTP/2
    may cause connection issues or isn't supported by the proxy.
    """
    import httpx as _httpx

    return _httpx.AsyncClient(http2=False, **kwargs)


def create_client_for_provider(provider: str, **kwargs: Any) -> httpx.AsyncClient:
    """Create an httpx AsyncClient appropriate for the given provider."""
    config = get_provider_transport(provider)
    if config.http2:
        return create_http2_client(**kwargs)
    return create_http1_client(**kwargs)


# ---------------------------------------------------------------------------
# Rate-limit header hooks
# ---------------------------------------------------------------------------


def create_ratelimit_hook(
    receiver: HeaderFeedbackReceiver,
) -> dict[str, list[Any]]:
    """Create httpx event hooks that extract rate-limit headers.

    The hook reads ``x-ratelimit-remaining-requests`` and
    ``x-ratelimit-limit-requests`` from each response and forwards
    them to ``receiver.on_header_feedback(remaining, limit)``.

    Returns a dict suitable for passing as ``event_hooks`` to httpx clients::

        hooks = create_ratelimit_hook(semaphore)
        client = httpx.AsyncClient(http2=True, event_hooks=hooks)
    """
    import httpx as _httpx

    async def _on_response(response: _httpx.Response) -> None:
        remaining_raw = response.headers.get("x-ratelimit-remaining-requests")
        limit_raw = response.headers.get("x-ratelimit-limit-requests")
        if remaining_raw is None or limit_raw is None:
            return
        try:
            remaining = int(remaining_raw)
            limit = int(limit_raw)
        except (ValueError, TypeError):
            return
        if limit > 0:
            receiver.on_header_feedback(remaining, limit)

    return {"response": [_on_response]}
