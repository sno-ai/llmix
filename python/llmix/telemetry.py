"""Telemetry plugin interface for LLMix.

Defines a Protocol that any telemetry backend (Helicone, Langfuse, custom)
can implement. llmix never imports a concrete telemetry SDK — the caller
picks the implementation at construction time, or passes None to disable.

Usage:
    # Standard Helicone (public users)
    plugin = HeliconePlugin(api_key="hk-...", environment="prod")
    pipeline = CallPipeline(config, telemetry=plugin)

    # Self-hosted / custom
    plugin = SnoHeliconePlugin(base_url="http://helicone.sno.ai:8585")
    pipeline = CallPipeline(config, telemetry=plugin)

    # No telemetry
    pipeline = CallPipeline(config, telemetry=None)
"""

from typing import Any, Protocol


class TelemetryPlugin(Protocol):
    """Plugin interface for LLM call telemetry routing and observability.

    The plugin is self-contained — it holds its own config (API key, base URL,
    environment). llmix only calls the methods; it never reads plugin internals.
    """

    def is_enabled(self) -> bool:
        """Check if telemetry is configured and available."""
        ...

    def get_proxy_url(self, provider: str) -> str:
        """Get the telemetry proxy URL for a given LLM provider."""
        ...

    def get_headers(self, *, app: str, module: str) -> dict[str, str]:
        """Build telemetry headers (auth, property tags, stream usage, etc.)."""
        ...

    def create_http_client(self, provider: str, enable_fallback: bool = True) -> Any:
        """Create an HTTP client with telemetry transport.

        Returns an httpx.AsyncClient (or compatible) with optional fallback
        to direct provider API on telemetry proxy failure. Returns None if
        no special transport is needed.
        """
        ...

    def log_cache_ratio(self, response: dict[str, Any], module: str, caller: str) -> None:
        """Log prompt cache hit/miss ratio from response usage data."""
        ...


__all__ = ["TelemetryPlugin"]
