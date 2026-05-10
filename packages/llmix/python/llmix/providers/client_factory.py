"""
LLM Client Factory - Centralized Singleton Management

Provides singleton instances of LLM clients for resource efficiency.
All code (memobase, lib, memu, tests) should use these factories instead
of creating client instances directly to share connection pools.

Architecture:
- AsyncOpenAIClient wraps OpenAI SDK's AsyncOpenAI
- Each AsyncOpenAI maintains an httpx connection pool (~100 connections)
- Singleton pattern ensures connection pool reuse across all API calls
- Reduces memory usage and prevents connection exhaustion under load

Usage:
    from llmix.providers.client_factory import get_async_openai_client

    client = get_async_openai_client()
    response = await client.response_completion(...)
"""

import logging
import threading

from llmix.providers._config import get_openai_api_key
from llmix.providers.openai_async_client import AsyncOpenAIClient

logger = logging.getLogger(__name__)


# Global singleton instances (lazy-initialized, protected by _client_lock)
_client_lock = threading.Lock()
_global_async_openai_client: AsyncOpenAIClient | None = None


def get_async_openai_client() -> AsyncOpenAIClient:
    """
    Get or create global AsyncOpenAIClient singleton instance.

    Returns singleton AsyncOpenAIClient configured with:
    - API key from OPENAI_API_KEY environment variable
    - Helicone observability support (auto-detected from config)
    - Default model from config (gpt-5-mini)
    - Lazy-loaded internal AsyncOpenAI SDK client with connection pool

    This function ensures all code shares the same AsyncOpenAI connection pool,
    preventing resource waste and connection exhaustion.

    Returns:
        AsyncOpenAIClient: Singleton instance of unified async OpenAI client

    Raises:
        ValueError: If OPENAI_API_KEY is not set in environment

    Example:
        >>> client = get_async_openai_client()
        >>> response = await client.response_completion(
        ...     input="Hello",
        ...     model="gpt-5-mini"
        ... )
    """
    global _global_async_openai_client  # pylint: disable=global-statement

    if _global_async_openai_client is None:
        with _client_lock:
            if _global_async_openai_client is None:
                api_key = get_openai_api_key()
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable is required for OpenAI client")

                _global_async_openai_client = AsyncOpenAIClient(
                    api_key=api_key,
                    base_url=None,  # Let unified client use default base URL from config
                    model=None,  # Let unified client use default model from config
                )
                logger.info("[LLM-FACTORY] Initialized AsyncOpenAIClient singleton")

    return _global_async_openai_client


def reset_async_openai_client() -> None:
    """
    Reset the global AsyncOpenAI client singleton.

    Forces recreation with updated configuration on next call to
    get_async_openai_client(). Useful for testing or when config changes.

    Warning:
        This will close the existing connection pool. Only call when necessary
        (e.g., during testing or after config reload).
    """
    global _global_async_openai_client  # pylint: disable=global-statement
    with _client_lock:
        _global_async_openai_client = None
    logger.info("[LLM-FACTORY] Reset AsyncOpenAIClient singleton - will be recreated with current configuration")
