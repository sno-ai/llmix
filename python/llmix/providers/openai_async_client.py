"""
Asynchronous OpenAI Client Implementation

Provides comprehensive async OpenAI API support with:
- Responses API (response_completion method)
- Chat Completions API (chat_completion method)
- Function calling with strict schema enforcement
- Aggressive prompt caching optimization
- Exponential backoff retry logic
- Helicone observability integration
- GPT-5 series reasoning parameters

All features identical to OpenAIClient but with async/await support.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from functools import lru_cache
from typing import Any

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

from llmix.providers._config import DEFAULT_LLM_TIMEOUT, get_openai_api_key, get_openai_base_url, get_openai_gpt5_reasoning_effort
from llmix.providers.base import BaseLLMClient, LLMResponse
from llmix.providers.openai_common import (
    apply_gpt5_chat_parameters,
    apply_gpt5_response_parameters,
    build_retry_config,
    extract_output_text,
    extract_tool_calls_from_response,
    extract_usage_dict,
    inject_strict_mode_chat_tools,
    inject_strict_mode_response_tools,
    jittered_delay,
    log_usage_info,
    prepare_messages,
    process_output_array,
)

# Hardcoded defaults replacing legacy MeMu config
_DEFAULT_MAX_OUTPUT_TOKENS = 16384
_DEFAULT_MODEL = "gpt-5-mini"

logger = logging.getLogger(__name__)


_helicone_logged_once = False
_helicone_log_lock = threading.Lock()


@lru_cache(maxsize=1)
def _import_helicone_module() -> Any | None:
    """Import Helicone lazily so OpenAI client works without telemetry extras."""
    try:
        from lib.telemetry import helicone as helicone_module
    except ImportError:
        return None
    return helicone_module


def _log_helicone_route_once(message: str) -> None:
    """Log Helicone/OpenAI routing once across threads/tasks."""
    global _helicone_logged_once
    with _helicone_log_lock:
        if not _helicone_logged_once:
            logger.info(message)
            _helicone_logged_once = True


class AsyncOpenAIClient(BaseLLMClient):
    """
    Asynchronous OpenAI Client Implementation

    Provides comprehensive async OpenAI API support with:
    - Responses API (response_completion method)
    - Chat Completions API (chat_completion method)
    - Function calling with strict schema enforcement
    - Aggressive prompt caching optimization
    - Exponential backoff retry logic
    - Helicone observability integration
    - GPT-5 series reasoning parameters

    All features identical to OpenAIClient but with async/await support.
    """

    # Default timeout for API calls from centralized config
    DEFAULT_TIMEOUT = DEFAULT_LLM_TIMEOUT

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        default_headers: dict[str, str] | None = None,
        environment: str = "dev",
        helicone_cache_enabled: bool = False,
        helicone_api_key: str | None = None,
        **kwargs: Any,
    ):
        """
        Initialize Asynchronous OpenAI Client

        Args:
            api_key: OpenAI API key (retrieved from environment if None)
            base_url: API base URL (supports custom endpoints)
            model: Default model name (defaults to config value)
            temperature: Default temperature for this client instance
            reasoning_effort: Reasoning effort for GPT-5 series
                (minimal/low/medium/high, retrieved from config if None)
            default_headers: Custom default headers for all requests. When provided,
                bypasses automatic Helicone header generation. Used by LLMix
                for pre-configured Helicone routing.
            environment: Environment name for Helicone headers (default: dev)
            helicone_cache_enabled: Enable Helicone response caching (default: False)
            helicone_api_key: Helicone API key for auto-detection path (Priority 2).
                When None, auto-detection is disabled and LLMix pre-configured headers
                (Priority 1) or direct base_url (Priority 3) are used instead.
            **kwargs: Additional configuration parameters

        Raises:
            ValueError: If API key is not provided and not in environment
        """
        # Apply defaults if not provided
        if model is None:
            model = _DEFAULT_MODEL
        if temperature is None:
            temperature = 1.0  # GPT-5 series always uses temperature=1
        if reasoning_effort is None:
            reasoning_effort = get_openai_gpt5_reasoning_effort()

        super().__init__(model=model, **kwargs)
        self.default_temperature: float = float(temperature)
        self.reasoning_effort = reasoning_effort

        self.api_key = api_key or get_openai_api_key()
        self.base_url = base_url or get_openai_base_url()
        self._default_headers = default_headers
        self._environment = environment
        self._helicone_cache_enabled = helicone_cache_enabled
        self._helicone_api_key = helicone_api_key

        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable or pass api_key parameter.")

        # Lazy load AsyncOpenAI client with thread-safe initialization
        # Note: Using threading.Lock (not asyncio.Lock) because the client property is synchronous
        # and AsyncOpenAI() initialization is synchronous. asyncio.Lock would require await.
        self._client: AsyncOpenAI | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy load AsyncOpenAI client with Helicone support"""
        if self._client is None:
            with self._client_lock:
                # Double-check locking pattern
                if self._client is not None:
                    return self._client

                base_url: str | None = None
                default_headers: dict[str, str] = {}
                helicone_lib = _import_helicone_module()

                # Priority 1: Custom headers provided (LLMix path with pre-configured Helicone)
                if self._default_headers is not None:
                    base_url = self.base_url
                    default_headers = self._default_headers
                    if base_url:
                        _log_helicone_route_once(f"Using pre-configured headers, routing through {base_url}")
                # Priority 2: Auto-detect Helicone (requires helicone_api_key to be passed)
                elif helicone_lib is not None and helicone_lib.is_helicone_enabled(self._helicone_api_key):
                    base_url = helicone_lib.get_helicone_url('openai')
                    default_headers = helicone_lib.get_helicone_headers(
                        app='sno-cortex', module='umi-async', environment=self._environment, api_key=self._helicone_api_key
                    )
                    if self._helicone_cache_enabled:
                        default_headers['Helicone-Cache-Enabled'] = 'true'
                        default_headers['Cache-Control'] = 'max-age=86400'
                    _log_helicone_route_once(f"Helicone enabled, routing through {base_url}")
                # Priority 3: Configured base_url
                elif self.base_url:
                    base_url = self.base_url
                    logger.info(f"Using configured base URL for OpenAI: {base_url}")

                # Create AsyncOpenAI client with enhanced configuration
                client_params: dict[str, Any] = {"api_key": self.api_key}
                if base_url:
                    client_params["base_url"] = base_url
                if default_headers:
                    client_params["default_headers"] = default_headers

                self._client = AsyncOpenAI(**client_params)

        return self._client

    async def chat_completion(  # pylint: disable=invalid-overridden-method
        self, messages: list[dict[str, str]], model: str | None = None, temperature: float | None = None, max_tokens: int | None = None, **kwargs: Any
    ) -> LLMResponse:
        """
        Execute async OpenAI chat completion with enhanced features

        Args:
            messages: List of conversation messages
            model: Model name (uses default if None)
            temperature: Generation temperature (uses instance default if None)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional parameters (tools, tool_choice, response_format, etc.)

        Returns:
            LLMResponse: Response with content, usage, model, and success status
        """
        model = self.get_model(model)
        resolved_max_tokens = self._get_max_tokens(max_tokens)

        # Preprocess messages
        processed_messages = prepare_messages(messages)

        # Prepare API arguments
        resolved_temperature = temperature if temperature is not None else self.default_temperature
        api_kwargs: dict[str, Any] = {
            "model": model,
            "messages": processed_messages,
            "temperature": resolved_temperature,
            "timeout": self.DEFAULT_TIMEOUT,
        }

        # Apply GPT-5 series specific parameters
        apply_gpt5_chat_parameters(model, api_kwargs, self.default_temperature, self.reasoning_effort, resolved_max_tokens)

        # Add function calling parameters if provided
        if "tools" in kwargs:
            api_kwargs["tools"] = inject_strict_mode_chat_tools(kwargs["tools"])

        if "tool_choice" in kwargs:
            api_kwargs["tool_choice"] = kwargs["tool_choice"]

        # Add response_format parameter for structured output
        if "response_format" in kwargs:
            api_kwargs["response_format"] = kwargs["response_format"]

        # Execute with retry logic
        return await self._execute_with_retry(api_kwargs, model)

    async def _execute_with_retry(self, api_kwargs: dict[str, Any], model: str) -> LLMResponse:
        """
        Execute async API call with retry logic and exponential backoff

        Args:
            api_kwargs: API call parameters
            model: Model name for logging and error handling

        Returns:
            LLMResponse: Response or error result
        """
        retry_config = build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                response = await self.client.chat.completions.create(**api_kwargs)

                # Log usage information
                log_usage_info(model, response, attempt, api_type="chat")

                # Guard against empty choices array (transient server issues, malformed responses)
                if not response.choices:
                    return self._handle_error(Exception("OpenAI returned no choices"), model)

                first_choice = response.choices[0]
                message = first_choice.message

                # Build response data
                # CRITICAL: content can be None when tool_calls are present (OpenAI API behavior)
                # Default to empty string to maintain LLMResponse.content type contract
                content = message.content if message and message.content else ""
                response_data: dict[str, Any] = {"content": content, "usage": extract_usage_dict(response), "model": response.model, "success": True}

                # Include tool calls if present
                if message and getattr(message, "tool_calls", None):
                    response_data["tool_calls"] = message.tool_calls

                return LLMResponse(**response_data)

            except Exception as err:
                # Retry only known-transient OpenAI error types
                is_retryable = isinstance(err, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))
                if not is_retryable:
                    logger.error(f"Non-retryable OpenAI error: {err}")
                    return self._handle_error(err, model)

                # Last attempt - return error response
                if attempt == retry_config["attempts"]:
                    logger.error(f"OpenAI generation failed after {retry_config['attempts']} attempts: {err}")
                    return self._handle_error(err, model)

                # Wait before retrying (use asyncio.sleep for async)
                logger.warning(f"OpenAI attempt {attempt} failed: {err}. Retrying in {delay}s...")
                await asyncio.sleep(jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        # Should never reach here
        return self._handle_error(Exception("Unexpected error in OpenAI completion"), model)

    async def response_completion(  # pylint: disable=redefined-builtin,too-many-arguments,too-many-positional-arguments,too-many-locals,invalid-overridden-method
        self,
        input: str | list[dict[str, Any]],
        model: str | None = None,
        instructions: str | None = None,
        text: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool | None = None,
        allowed_tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        store: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """
        Async Responses API implementation (OpenAI SDK 2.0+) with FULL cache optimization

        CRITICAL CACHE OPTIMIZATION: Returns full response.output array for iterative calls
        Pattern: input_list += response.output  # Enables aggressive prompt caching

        Args:
            input: User input (string or message array). For iterative calls, pass growing array.
            model: Model to use (defaults to instance model)
            instructions: System instructions (replaces system messages in chat.completions)
            text: Structured output configuration via text.format (replaces response_format)
            tools: Tool definitions for function calling
            tool_choice: Control function calling: "auto", "required", or
                {"type": "function", "name": "func"}
            parallel_tool_calls: True=concurrent calls (default), False=sequential
                (0 or 1 per turn)
            allowed_tools: Restrict to subset of tools (CRITICAL: keeps full tools
                unchanged for caching)
            temperature: 0.0-2.0 for standard models (GPT-5 requires 1.0 from config)
            max_output_tokens: Max output tokens (Response API uses max_output_tokens
                for all models)
            store: Store conversation for training (default False from config for privacy)
            reasoning_effort: Per-call reasoning override (minimal|low|medium|high)

        Returns:
            LLMResponse with:
            - content: Extracted text (convenience access)
            - output: FULL output array (CRITICAL for cache optimization in iterative calls)
            - usage: Token usage including cached_tokens
            - model: Model used
            - success: Boolean status
        """
        model = self.get_model(model)

        # Apply defaults
        if store is None:
            store = False  # Privacy: never store by default
        resolved_temperature: float = temperature if temperature is not None else self.default_temperature
        if max_output_tokens is None:
            max_output_tokens = _DEFAULT_MAX_OUTPUT_TOKENS
        if tool_choice is None and tools:
            tool_choice = "auto"
        if parallel_tool_calls is None and tools:
            parallel_tool_calls = False  # Sequential operations (memory agent pattern)

        # Prepare API arguments for Responses API
        api_kwargs: dict[str, Any] = {"model": model, "input": input, "store": store, "timeout": self.DEFAULT_TIMEOUT}

        if instructions:
            api_kwargs["instructions"] = instructions

        if text:
            api_kwargs["text"] = text

        if tools:
            api_kwargs["tools"] = inject_strict_mode_response_tools(tools)

        # Add tool control parameters
        if tool_choice is not None:
            api_kwargs["tool_choice"] = tool_choice

        if parallel_tool_calls is not None:
            api_kwargs["parallel_tool_calls"] = parallel_tool_calls

        if allowed_tools is not None:
            api_kwargs["allowed_tools"] = allowed_tools

        # Apply GPT-5 series specific parameters
        effective_reasoning = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
        apply_gpt5_response_parameters(model, api_kwargs, resolved_temperature, self.default_temperature, effective_reasoning, max_output_tokens)

        # Execute with retry logic
        return await self._execute_response_with_retry(api_kwargs, model)

    async def _execute_response_with_retry(self, api_kwargs: dict[str, Any], model: str) -> LLMResponse:
        """
        Execute async Responses API call with retry logic and FULL output array for
        cache optimization

        Args:
            api_kwargs: API call parameters
            model: Model name for logging and error handling

        Returns:
            LLMResponse with full output array for cache optimization
        """
        retry_config = build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                response = await self.client.responses.create(**api_kwargs)

                # Extract output text
                output_text = extract_output_text(response)

                # CRITICAL - Preserve full output array for cache optimization
                output_array = process_output_array(response)

                # Extract function calls from Response API output array
                tool_calls = extract_tool_calls_from_response(response)

                # Log usage information
                log_usage_info(model, response, attempt, api_type="response")

                return LLMResponse(
                    content=output_text or "",
                    usage=extract_usage_dict(response),
                    model=response.model if hasattr(response, "model") else model,
                    success=True,
                    output=output_array,
                    tool_calls=tool_calls,
                )

            except Exception as err:
                # Retry only known-transient OpenAI error types
                is_retryable = isinstance(err, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))
                if not is_retryable:
                    logger.error(f"Non-retryable OpenAI Responses error: {err}")
                    return self._handle_error(err, model)

                # Last attempt - return error response
                if attempt == retry_config["attempts"]:
                    logger.error(f"OpenAI Responses failed after {retry_config['attempts']} attempts: {err}")
                    return self._handle_error(err, model)

                # Wait before retrying (use asyncio.sleep for async)
                logger.warning(f"OpenAI Responses attempt {attempt} failed: {err}. Retrying in {delay}s...")
                await asyncio.sleep(jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        # Should never reach here
        return self._handle_error(Exception("Unexpected error in Responses API"), model)

    def _get_default_model(self) -> str:
        """Get OpenAI default model from config"""
        return _DEFAULT_MODEL

    @classmethod
    def from_env(cls) -> AsyncOpenAIClient:
        """Create async OpenAI client from environment variables"""
        return cls()

    async def close(self) -> None:
        """Close underlying AsyncOpenAI HTTP client and release resources."""
        client_to_close: AsyncOpenAI | None = None
        with self._client_lock:
            if self._client is not None:
                client_to_close = self._client
                self._client = None

        if client_to_close is not None:
            await client_to_close.close()

    def __str__(self) -> str:
        """String representation of the client"""
        return f"AsyncOpenAIClient(model={self.default_model}, base_url={self.base_url})"
