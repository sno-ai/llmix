"""
LLM Basic Abstract Classes and Data Structures

Shared base classes for all LLM clients (OpenAI, Gemini, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Default max output tokens (replaces legacy MeMu config constant)
_DEFAULT_MAX_OUTPUT_TOKENS = 16384


@dataclass
class LLMResponse:
    """LLM Response Result"""

    content: str
    usage: dict[str, int]
    model: str
    success: bool
    error: str | None = None
    tool_calls: list[Any] | None = None  # Support for function calling (Chat Completions API)
    output: list[Any] | None = None  # CRITICAL: Full output array for Response API caching optimization

    def __bool__(self) -> bool:
        """Enable response object to be used as boolean value"""
        return self.success

    def __str__(self) -> str:
        """String representation returns content"""
        return self.content


class BaseLLMClient(ABC):
    """LLM Client Base Class"""

    def __init__(self, model: str | None = None, **kwargs):
        """
        Initialize LLM Client

        Args:
            model: Default model name
            **kwargs: Other configuration parameters
        """
        self.default_model = model
        self.config = kwargs

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
        **kwargs,
    ) -> LLMResponse:
        """
        Chat Completion Interface

        Args:
            messages: List of conversation messages
            model: Model name, uses default_model if None
            temperature: Generation temperature
            max_tokens: Maximum number of tokens
            **kwargs: Other parameters

        Returns:
            LLMResponse: Response result
        """
        ...

    @abstractmethod
    async def response_completion(
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
        Response API Interface (OpenAI SDK 2.0+)

        CRITICAL CACHE OPTIMIZATION: Returns full response.output array for iterative calls
        Pattern: input_list += response.output  # Enables aggressive prompt caching

        Args:
            input: User input (string or message array)
            model: Model to use
            instructions: System instructions
            text: Structured output configuration
            tools: Tool definitions for function calling
            tool_choice: Control function calling
            parallel_tool_calls: Control concurrent vs sequential function calls
            allowed_tools: Restrict to subset of tools
            temperature: Temperature control
            max_output_tokens: Max output tokens
            store: Store conversation for training
            reasoning_effort: Reasoning effort level

        Returns:
            LLMResponse with full output array for cache optimization
        """
        ...

    def get_model(self, model: str | None = None) -> str:
        """Get the model name to use"""
        return model or self.default_model or self._get_default_model()

    @abstractmethod
    def _get_default_model(self) -> str:
        """Get provider's default model"""
        ...

    def _prepare_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Preprocess message format, can be overridden by subclasses"""
        return messages

    def _get_max_tokens(self, max_tokens: int | None = None) -> int:
        """Get max tokens value from environment or use provided/default value"""
        if max_tokens is not None:
            return max_tokens

        # Use default max output tokens
        return _DEFAULT_MAX_OUTPUT_TOKENS

    def _handle_error(self, error: Exception, model: str) -> LLMResponse:
        """Unified error handling"""
        return LLMResponse(content="", usage={}, model=model, success=False, error=str(error))


__all__ = ["BaseLLMClient", "LLMResponse"]
