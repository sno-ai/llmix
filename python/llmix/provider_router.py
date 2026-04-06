"""Provider client creation and routing for LLMix.

Extracted from lib/llmix/client.py as part of god class decomposition.
Encapsulates all provider-specific client creation, Helicone proxy routing,
and LRU client caching.

Usage:
    router = ProviderRouter(api_keys=api_keys, helicone=helicone, provider_urls=provider_urls)
    client = router.get_provider_client(provider="openai", model="gpt-4o", ...)
"""

import asyncio
import inspect
import logging
import threading
from collections import OrderedDict
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, cast

from llmix.env import (
    get_deepinfra_api_key,
    get_environment,
    get_gemini_api_key,
    get_internal_service_secret,
    get_novita_api_key,
    get_openai_api_key,
    get_openrouter_api_key,
    get_together_api_key,
)
from llmix.types import ApiKeysConfig, CachingStrategy, HeliconeConfig, Provider, ProviderUrlConfig

if TYPE_CHECKING:
    from llmix.providers.base import BaseLLMClient
    from lib.telemetry.helicone import Provider as HeliconeProvider

logger = logging.getLogger(__name__)

__all__ = ["MAX_CLIENT_CACHE_SIZE", "ProviderRouter", "is_embedding_model"]

ClientCacheKey = tuple[Provider, str, CachingStrategy, str | None, str, bool, bool | None, int | None, str | None]

# Maximum number of cached provider clients
MAX_CLIENT_CACHE_SIZE = 64

# Providers that support CF AI Gateway response caching
_CF_GATEWAY_PROVIDERS: set[str] = {"openai", "anthropic"}

# DeepSeek model name to OpenRouter format mappings
_DEEPSEEK_MODEL_MAPPINGS: dict[str, str] = {
    "deepseek-chat": "deepseek/deepseek-chat-v3-0324",
    "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
    "deepseek-v3.2-speciale": "deepseek/deepseek-chat-v3-0324:free",
    "deepseek-reasoner": "deepseek/deepseek-reasoner",
}

# Anthropic model name to OpenRouter format mappings
_ANTHROPIC_MODEL_MAPPINGS: dict[str, str] = {
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "claude-opus-4-6": "anthropic/claude-opus-4-6",
    "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
    "claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
    "claude-3-opus": "anthropic/claude-3-opus",
}


def _resolve_google_api_key(api_keys: ApiKeysConfig | None) -> str | None:
    """Resolve the Gemini API key from injected config or the standard env var."""
    if api_keys and api_keys.get("google"):
        return api_keys.get("google")
    return get_gemini_api_key()


def _resolve_helicone_environment(helicone: HeliconeConfig | None) -> str:
    """Resolve the Helicone environment label from config or runtime env vars."""
    if helicone and helicone.get("environment"):
        return helicone.get("environment", "dev")
    return get_environment()


def is_embedding_model(model: str) -> bool:
    """Check if a model is an embedding model."""
    return "embedding" in model.lower()


def _map_deepseek_model(model: str) -> str:
    """Map DeepSeek model name to OpenRouter format."""
    if model.startswith("deepseek/"):
        return model
    return _DEEPSEEK_MODEL_MAPPINGS.get(model, f"deepseek/{model}")


def _map_anthropic_model(model: str) -> str:
    """Map Anthropic model name to OpenRouter format."""
    mapped = _ANTHROPIC_MODEL_MAPPINGS.get(model)
    if mapped:
        return mapped
    return model if model.startswith("anthropic/") else f"anthropic/{model}"


class ProviderRouter:
    """Manages provider client creation, caching, and Helicone proxy routing.

    Extracted from LLMClient to isolate provider-specific concerns.
    Holds an LRU-bounded cache of provider client instances.
    """

    def __init__(
        self, api_keys: ApiKeysConfig | None = None, helicone: HeliconeConfig | None = None, provider_urls: ProviderUrlConfig | None = None
    ) -> None:
        self._api_keys = api_keys
        self._helicone = helicone
        self._provider_urls = provider_urls
        self._client_cache: OrderedDict[ClientCacheKey, BaseLLMClient] = OrderedDict()
        self._client_initializing: dict[ClientCacheKey, threading.Event] = {}
        self._cache_lock = threading.Lock()
        self._eviction_tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        """Close all cached provider clients and release their resources."""
        with self._cache_lock:
            eviction_tasks = tuple(self._eviction_tasks)
            self._eviction_tasks.clear()
            cached_clients = list(self._client_cache.items())
            self._client_cache.clear()

        if eviction_tasks:
            await asyncio.gather(*eviction_tasks, return_exceptions=True)

        for cache_key, client in cached_clients:
            await self._close_client_async(client, cache_key)

    def get_provider_client(
        self,
        provider: Provider,
        model: str,
        caching_strategy: CachingStrategy,
        cache_key: str | None,
        helicone_module: str,
        helicone_enabled_yaml: bool = True,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
        gpu_path: str | None = None,
    ) -> BaseLLMClient:
        """Get or create a provider client instance with LRU caching.

        Args:
            provider: LLM provider name
            model: Model ID
            caching_strategy: Caching strategy (native, gateway, disabled)
            cache_key: Cache key for native strategy
            helicone_module: Module name for Helicone tracking
            helicone_enabled_yaml: Whether Helicone is enabled in YAML config
            enable_thinking: DeepInfra/GPU thinking mode
            thinking_budget: DeepInfra/GPU thinking token budget
            gpu_path: GPU routing path for snogpu

        Returns:
            Provider client instance
        """
        client_cache_key: ClientCacheKey = (
            provider,
            model,
            caching_strategy,
            cache_key,
            helicone_module,
            helicone_enabled_yaml,
            enable_thinking,
            thinking_budget,
            gpu_path,
        )
        while True:
            with self._cache_lock:
                existing_client = self._client_cache.get(client_cache_key)
                if existing_client is not None:
                    self._client_cache.move_to_end(client_cache_key)
                    return existing_client

                wait_event = self._client_initializing.get(client_cache_key)
                if wait_event is None:
                    wait_event = threading.Event()
                    self._client_initializing[client_cache_key] = wait_event
                    should_create = True
                else:
                    should_create = False

            if not should_create:
                wait_event.wait()
                continue

            client: BaseLLMClient | None = None
            client_cached = False
            clients_to_close: list[tuple[ClientCacheKey, BaseLLMClient]] = []
            try:
                client = self._create_provider_client(
                    provider, model, caching_strategy, cache_key, helicone_module, helicone_enabled_yaml, enable_thinking, thinking_budget, gpu_path
                )
                with self._cache_lock:
                    self._client_cache[client_cache_key] = client
                    client_cached = True
                    clients_to_close.extend(self._evict_oldest_clients())
                    wait_event = self._client_initializing.pop(client_cache_key, None)
                    if wait_event is not None:
                        wait_event.set()
                for evicted_key, evicted_client in clients_to_close:
                    self._close_client_background(evicted_client, evicted_key)
                return client
            except Exception:
                if client is not None and not client_cached:
                    self._close_client_background(client, client_cache_key)
                raise
            finally:
                with self._cache_lock:
                    wait_event = self._client_initializing.pop(client_cache_key, None)
                    if wait_event is not None:
                        wait_event.set()

    def _create_provider_client(
        self,
        provider: Provider,
        model: str,
        caching_strategy: CachingStrategy,
        cache_key: str | None,
        helicone_module: str,
        helicone_enabled_yaml: bool,
        enable_thinking: bool | None,
        thinking_budget: int | None,
        gpu_path: str | None,
    ) -> BaseLLMClient:
        """Dispatch to the correct provider-specific client factory."""
        if provider == "openai":
            return self._create_openai_client(model, caching_strategy, cache_key, helicone_module, helicone_enabled_yaml)
        if provider == "google":
            return self._create_gemini_client(model, caching_strategy, helicone_module, helicone_enabled_yaml)
        if provider == "anthropic":
            return self._create_openrouter_client(
                _map_anthropic_model(model), "Anthropic", caching_strategy, cache_key, helicone_module, helicone_enabled_yaml
            )
        if provider == "deepseek":
            return self._create_openrouter_client(
                _map_deepseek_model(model), "DeepSeek", caching_strategy, cache_key, helicone_module, helicone_enabled_yaml
            )
        if provider == "deepinfra":
            return self._create_deepinfra_client(
                model, caching_strategy, cache_key, helicone_module, helicone_enabled_yaml, enable_thinking, thinking_budget
            )
        if provider == "together":
            return self._create_together_client(model, caching_strategy, cache_key, helicone_module, helicone_enabled_yaml)
        if provider == "novita":
            return self._create_novita_client(
                model,
                caching_strategy,
                cache_key,
                helicone_module,
                helicone_enabled_yaml,
                enable_thinking,
                thinking_budget,
            )
        if provider == "snogpu":
            return self._create_gpu_client(
                model, caching_strategy, cache_key, helicone_module, helicone_enabled_yaml, enable_thinking, thinking_budget, gpu_path
            )
        raise ValueError(
            f"[LLMix] Unsupported provider: {provider}. Supported: openai, google, anthropic, deepseek, deepinfra, together, novita, snogpu"
        )

    def _evict_oldest_clients(self) -> list[tuple[ClientCacheKey, BaseLLMClient]]:
        """Evict oldest cache entries beyond MAX_CLIENT_CACHE_SIZE. Must hold _cache_lock."""
        evicted_clients: list[tuple[ClientCacheKey, BaseLLMClient]] = []
        while len(self._client_cache) > MAX_CLIENT_CACHE_SIZE:
            evicted_key, evicted_client = self._client_cache.popitem(last=False)
            evicted_clients.append((evicted_key, evicted_client))
            logger.debug("[LLMix] Evicted cached client: %s", evicted_key)
        return evicted_clients

    async def _close_client_async(self, client: BaseLLMClient, cache_key: ClientCacheKey) -> None:
        """Close a cached client without holding the cache lock."""
        close_method = getattr(client, "close", None)
        if not callable(close_method):
            return

        try:
            maybe_awaitable = close_method()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.warning("[LLMix] Failed to close client %s", cache_key, exc_info=True)

    def _close_client_background(self, client: BaseLLMClient, cache_key: ClientCacheKey) -> None:
        """Close a client from sync code without assuming an active event loop."""
        close_method = getattr(client, "close", None)
        if not callable(close_method):
            return

        try:
            maybe_awaitable = close_method()
        except Exception:
            logger.warning("[LLMix] Failed to close client %s", cache_key, exc_info=True)
            return

        if not inspect.isawaitable(maybe_awaitable):
            return

        # Narrow from Awaitable to Coroutine for asyncio.run / create_task
        coro = cast("Coroutine[Any, Any, None]", maybe_awaitable)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[LLMix] No running event loop while closing client %s; using best-effort temporary loop for cleanup", cache_key)
            try:
                asyncio.run(coro)
            except Exception:
                logger.warning("[LLMix] Failed to close client %s", cache_key, exc_info=True)
            return

        task = loop.create_task(coro)
        with self._cache_lock:
            self._eviction_tasks.add(task)
        task.add_done_callback(self._discard_eviction_task)

    def _discard_eviction_task(self, task: asyncio.Task[None]) -> None:
        """Remove completed eviction tasks from the tracking set."""
        with self._cache_lock:
            self._eviction_tasks.discard(task)

    def _resolve_helicone_routing(
        self, provider: HeliconeProvider, helicone_enabled_yaml: bool, caching_strategy: CachingStrategy, cache_key: str | None, helicone_module: str
    ) -> tuple[str, dict[str, str]] | None:
        """Resolve Helicone proxy routing for logging and observability.

        Returns (base_url, headers) if Helicone should be used, None otherwise.
        """
        from lib.telemetry.helicone import get_helicone_headers, get_helicone_url, is_helicone_enabled

        if not helicone_enabled_yaml:
            return None

        helicone_api_key = self._helicone.get("api_key") if self._helicone else None
        if not is_helicone_enabled(helicone_api_key):
            return None

        helicone_base_url = self._helicone.get("base_url") if self._helicone else None
        if not helicone_base_url:
            helicone_base_url = get_helicone_url(provider)

        env = _resolve_helicone_environment(self._helicone)
        headers = get_helicone_headers(app="sno-cortex", module=helicone_module, environment=env, api_key=helicone_api_key)

        if provider not in _CF_GATEWAY_PROVIDERS or caching_strategy == "disabled":
            headers["Helicone-Skip-CF-Gateway"] = "true"

        if caching_strategy == "native":
            headers["Helicone-Cache-Enabled"] = "true"
            headers["Cache-Control"] = "max-age=86400"
            if cache_key:
                headers["Helicone-Cache-Key"] = cache_key

        return (helicone_base_url, headers)

    def _create_openai_client(
        self, model: str, caching_strategy: CachingStrategy, cache_key: str | None, helicone_module: str, helicone_enabled_yaml: bool = True
    ) -> BaseLLMClient:
        """Create OpenAI client with appropriate configuration."""
        from llmix.providers.openai_async_client import AsyncOpenAIClient

        api_key = self._api_keys.get("openai") if self._api_keys else None
        if not api_key:
            api_key = get_openai_api_key()
        if not api_key:
            raise ValueError("[LLMix] OpenAI API key is required. Pass via api_keys config or OPENAI_API_KEY env var.")

        if not is_embedding_model(model):
            helicone = self._resolve_helicone_routing("openai", helicone_enabled_yaml, caching_strategy, cache_key, helicone_module)
            if helicone:
                routing_base_url, headers = helicone
                logger.info("[LLMix] Routing OpenAI via Helicone (cache: %s, key: %s)", caching_strategy, "custom" if cache_key else "auto")
                return AsyncOpenAIClient(api_key=api_key, base_url=routing_base_url, model=model, default_headers=headers)

        base_url: str | None = None
        if caching_strategy != "disabled" and self._provider_urls:
            base_url = self._provider_urls.get("openai_base_url")

        return AsyncOpenAIClient(api_key=api_key, base_url=base_url, model=model)

    def _create_gemini_client(
        self, model: str, caching_strategy: CachingStrategy, helicone_module: str = "llmix", helicone_enabled_yaml: bool = True
    ) -> BaseLLMClient:
        """Create Gemini client with Helicone logging and optional gateway routing."""
        try:
            from llmix.providers.gemini_async_client import AsyncGeminiClient
        except ImportError as e:
            raise ImportError(
                "[LLMix] Failed to import AsyncGeminiClient. Ensure google-generativeai package is installed: pip install google-generativeai"
            ) from e

        api_key = _resolve_google_api_key(self._api_keys)
        if not api_key:
            raise ValueError("[LLMix] Google API key is required. Pass via api_keys config or set GEMINI_API_KEY.")

        helicone = self._resolve_helicone_routing("google", helicone_enabled_yaml, caching_strategy, None, helicone_module)
        if helicone:
            routing_base_url, headers = helicone
            logger.info("[LLMix] Routing Gemini via Helicone (cache: %s)", caching_strategy)
            return AsyncGeminiClient(api_key=api_key, base_url=routing_base_url, model=model, default_headers=headers)

        base_url: str | None = None
        if caching_strategy != "disabled" and self._provider_urls:
            base_url = self._provider_urls.get("gemini_base_url")

        return AsyncGeminiClient(api_key=api_key, base_url=base_url, model=model)

    def _create_openrouter_client(
        self,
        model: str,
        provider_label: str,
        caching_strategy: CachingStrategy,
        cache_key: str | None,
        helicone_module: str = "llmix",
        helicone_enabled_yaml: bool = True,
    ) -> BaseLLMClient:
        """Create an OpenRouter-routed client (OpenAI-compatible API)."""
        from llmix.providers.openai_async_client import AsyncOpenAIClient

        api_key = self._api_keys.get("openrouter") if self._api_keys else None
        if not api_key:
            api_key = get_openrouter_api_key()
        if not api_key:
            raise ValueError(
                f"[LLMix] OpenRouter API key required for {provider_label} provider. Pass via api_keys config or OPENROUTER_API_KEY env var."
            )

        helicone = self._resolve_helicone_routing("openrouter", helicone_enabled_yaml, caching_strategy, cache_key, helicone_module)
        if helicone:
            base_url, headers = helicone
            logger.info("[LLMix] Routing %s via Helicone/OpenRouter (cache: %s)", provider_label, caching_strategy)
            return AsyncOpenAIClient(api_key=api_key, base_url=base_url, model=model, default_headers=headers)

        from llmix.provider_urls import OPENROUTER_BASE_URL

        base_url = OPENROUTER_BASE_URL
        if caching_strategy != "disabled" and self._provider_urls:
            base_url = self._provider_urls.get("openrouter_base_url", base_url)

        logger.info("[LLMix] Routing %s via OpenRouter: %s", provider_label, model)
        return AsyncOpenAIClient(api_key=api_key, base_url=base_url, model=model)

    def _create_deepinfra_client(
        self,
        model: str,
        caching_strategy: CachingStrategy,
        cache_key: str | None,
        helicone_module: str = "llmix",
        helicone_enabled_yaml: bool = True,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
    ) -> BaseLLMClient:
        """Create DeepInfra client via OpenAI-compatible Chat Completions API."""
        from llmix.providers.deepinfra_client import DeepInfraClient

        api_key = self._api_keys.get("deepinfra") if self._api_keys else None
        if not api_key:
            api_key = get_deepinfra_api_key()
        if not api_key:
            raise ValueError("[LLMix] DeepInfra API key is required. Pass via api_keys config or DEEPINFRA_API_KEY env var.")

        helicone = self._resolve_helicone_routing("deepinfra", helicone_enabled_yaml, caching_strategy, cache_key, helicone_module)
        if helicone:
            base_url, headers = helicone
            logger.info("[LLMix] Routing DeepInfra via Helicone (cache: %s, key: %s)", caching_strategy, "custom" if cache_key else "auto")

            from lib.telemetry.helicone import create_helicone_http_client

            http_client = create_helicone_http_client(provider="deepinfra", enable_fallback=True)

            return DeepInfraClient(
                api_key=api_key,
                base_url=base_url,
                model=model,
                enable_thinking=bool(enable_thinking),
                thinking_budget=thinking_budget,
                default_headers=headers,
                http_client=http_client,
            )

        logger.info("[LLMix] Creating DeepInfra client for model: %s", model)
        return DeepInfraClient(api_key=api_key, model=model, enable_thinking=bool(enable_thinking), thinking_budget=thinking_budget)

    def _create_together_client(
        self, model: str, caching_strategy: CachingStrategy, cache_key: str | None, helicone_module: str = "llmix", helicone_enabled_yaml: bool = True
    ) -> BaseLLMClient:
        """Create Together AI client via OpenAI-compatible Chat Completions API."""
        from llmix.providers.together_client import TogetherClient

        api_key = self._api_keys.get("together") if self._api_keys else None
        if not api_key:
            api_key = get_together_api_key()
        if not api_key:
            raise ValueError("[LLMix] Together API key is required. Pass via api_keys config or TOGETHER_API_KEY env var.")

        helicone = self._resolve_helicone_routing("together", helicone_enabled_yaml, caching_strategy, cache_key, helicone_module)
        if helicone:
            base_url, headers = helicone
            logger.info("[LLMix] Routing Together via Helicone (cache: %s, key: %s)", caching_strategy, "custom" if cache_key else "auto")

            from lib.telemetry.helicone import create_helicone_http_client

            http_client = create_helicone_http_client(provider="together", enable_fallback=True)

            return TogetherClient(api_key=api_key, base_url=base_url, model=model, default_headers=headers, http_client=http_client)

        logger.info("[LLMix] Creating Together client for model: %s", model)
        return TogetherClient(api_key=api_key, model=model)

    def _create_novita_client(
        self,
        model: str,
        caching_strategy: CachingStrategy,
        cache_key: str | None,
        helicone_module: str = "llmix",
        helicone_enabled_yaml: bool = True,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
    ) -> BaseLLMClient:
        """Create Novita AI client via OpenAI-compatible Chat Completions API."""
        from llmix.providers.novita_client import NovitaClient

        api_key = self._api_keys.get("novita") if self._api_keys else None
        if not api_key:
            api_key = get_novita_api_key()
        if not api_key:
            raise ValueError("[LLMix] Novita API key is required. Pass via api_keys config or NOVITA_API_KEY env var.")

        helicone = self._resolve_helicone_routing("novita", helicone_enabled_yaml, caching_strategy, cache_key, helicone_module)
        if helicone:
            base_url, headers = helicone
            logger.info("[LLMix] Routing Novita via Helicone (cache: %s, key: %s)", caching_strategy, "custom" if cache_key else "auto")

            from lib.telemetry.helicone import create_helicone_http_client

            http_client = create_helicone_http_client(provider="novita", enable_fallback=True)

            return NovitaClient(
                api_key=api_key,
                base_url=base_url,
                model=model,
                enable_thinking=bool(enable_thinking),
                thinking_budget=thinking_budget,
                default_headers=headers,
                http_client=http_client,
            )

        logger.info("[LLMix] Creating Novita client for model: %s (thinking: %s)", model, bool(enable_thinking))
        return NovitaClient(
            api_key=api_key,
            model=model,
            enable_thinking=bool(enable_thinking),
            thinking_budget=thinking_budget,
        )

    def _create_gpu_client(
        self,
        model: str,
        caching_strategy: CachingStrategy,
        cache_key: str | None,
        helicone_module: str = "llmix",
        helicone_enabled_yaml: bool = True,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
        gpu_path: str | None = None,
    ) -> BaseLLMClient:
        """Create on-prem GPU client for Qwen3.5-27B Dense inference."""
        from llmix.providers.onprem_gpu_client import GpuClient, build_gpu_base_url

        service_secret = self._api_keys.get("snogpu") if self._api_keys else None
        if not service_secret:
            service_secret = get_internal_service_secret()
        if not service_secret:
            raise ValueError("[LLMix] Service secret is required for GPU provider. Pass via api_keys config or INTERNAL_SERVICE_SECRET env var.")

        path_suffix = f"/{gpu_path}/v1" if gpu_path else "/v1"
        path_label = gpu_path or "legacy"

        helicone = self._resolve_helicone_routing("snogpu", helicone_enabled_yaml, caching_strategy, cache_key, helicone_module)
        if helicone:
            helicone_base_url, headers = helicone
            base_url = f"{helicone_base_url}{path_suffix}"
            logger.info("[LLMix] Routing snogpu/%s via Helicone (cache: %s, thinking: %s)", path_label, caching_strategy, bool(enable_thinking))

            from lib.telemetry.helicone import create_helicone_http_client

            http_client = create_helicone_http_client(provider="snogpu", enable_fallback=True)

            return GpuClient(
                service_secret=service_secret,
                base_url=base_url,
                model=model,
                enable_thinking=bool(enable_thinking),
                thinking_budget=thinking_budget,
                default_headers=headers,
                http_client=http_client,
            )

        direct_base_url = build_gpu_base_url(gpu_path)
        logger.info("[LLMix] Creating snogpu/%s client for model: %s (thinking: %s)", path_label, model, bool(enable_thinking))
        return GpuClient(
            service_secret=service_secret,
            base_url=direct_base_url,
            model=model,
            enable_thinking=bool(enable_thinking),
            thinking_budget=thinking_budget,
        )
