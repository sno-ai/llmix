"""
OpenRouter Client Implementation.

OpenRouter is OpenAI-compatible at the Chat Completions boundary, but it has
provider-specific model routing and response usage details. Keep that behavior
out of the OpenAI Responses client so both clients stay easier to reason about.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from openrouter import OpenRouter

from llmix.provider_urls import OPENROUTER_BASE_URL
from llmix.providers.base import BaseLLMClient, LLMResponse

logger = logging.getLogger(__name__)


class OpenRouterClient(BaseLLMClient):
    """Async OpenRouter client using the official OpenRouter SDK."""

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        default_headers: dict[str, str] | None = None,
        http_client: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, **kwargs)
        if not api_key:
            raise ValueError("OpenRouter API key is required. Set OPENROUTER_API_KEY or pass api_key.")
        self.api_key = api_key
        self.base_url = base_url
        self._default_headers = default_headers
        self._http_client = http_client
        self._client: OpenRouter | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> OpenRouter:
        """Lazy-initialize the official OpenRouter SDK client."""
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            if self._client is None:
                client_kwargs: dict[str, Any] = {
                    "api_key": self.api_key,
                    "server_url": self.base_url,
                }
                if self._http_client is not None:
                    client_kwargs["async_client"] = self._http_client
                self._client = OpenRouter(**client_kwargs)
            return self._client

    async def chat_completion(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = 0.0,
        max_tokens: int | None = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Execute an OpenRouter chat completion."""
        resolved_model = self.get_model(model)
        api_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._prepare_chat_messages(messages),
        }
        if temperature is not None:
            api_kwargs["temperature"] = temperature

        resolved_max_tokens = kwargs.pop("max_output_tokens", None)
        if resolved_max_tokens is None:
            resolved_max_tokens = max_tokens
        if resolved_max_tokens is not None:
            api_kwargs["max_tokens"] = resolved_max_tokens

        for param in (
            "top_p",
            "stop",
            "seed",
            "response_format",
            "presence_penalty",
            "frequency_penalty",
            "provider",
            "reasoning",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
        ):
            value = kwargs.get(param)
            if value is not None:
                api_kwargs[param] = value

        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            provider = extra_body.get("provider")
            if provider is not None and "provider" not in api_kwargs:
                api_kwargs["provider"] = provider
            reasoning = extra_body.get("reasoning")
            if reasoning is not None and "reasoning" not in api_kwargs:
                api_kwargs["reasoning"] = reasoning

        try:
            response = await self.client.chat.send_async(
                **api_kwargs,
                stream=False,
                server_url=self.base_url,
                http_headers=self._default_headers,
            )
            if not response.choices:
                return self._handle_error(Exception("OpenRouter returned no choices"), resolved_model)

            first_choice = response.choices[0]
            message = first_choice.message
            content = self._extract_content(message)

            return LLMResponse(
                content=content,
                usage=self._extract_usage(response),
                model=response.model or resolved_model,
                success=True,
                tool_calls=self._extract_tool_calls(message),
            )
        except Exception as err:
            logger.error("OpenRouter chat completion failed: %s", err)
            return self._handle_error(err, resolved_model)

    async def response_completion(  # type: ignore[override]
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
        **kwargs: Any,
    ) -> LLMResponse:
        """Map the Responses-style interface to OpenRouter chat completions."""
        _ = allowed_tools, store, reasoning_effort
        messages = self._messages_from_response_input(input, instructions)
        if not messages or not any(message["role"] != "system" and str(message.get("content", "")).strip() for message in messages):
            return self._handle_error(ValueError("No non-system text content after input conversion"), model or self._get_default_model())

        if text and isinstance(text, dict) and text.get("format"):
            response_format_value = text.get("format")
            if isinstance(response_format_value, str):
                kwargs.setdefault("response_format", {"type": response_format_value})
            elif isinstance(response_format_value, dict):
                kwargs.setdefault("response_format", response_format_value)

        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = parallel_tool_calls

        return await self.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature if temperature is not None else 0.0,
            max_tokens=max_output_tokens,
            **kwargs,
        )

    def _prepare_chat_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            prepared_message: dict[str, Any] = {"role": role}
            if "content" in message and message["content"] is not None:
                prepared_message["content"] = message["content"]
            elif role != "assistant" or "tool_calls" not in message:
                prepared_message["content"] = ""
            for key in ("tool_calls", "tool_call_id", "name"):
                if key in message:
                    prepared_message[key] = message[key]
            prepared.append(prepared_message)
        return prepared

    def _messages_from_response_input(self, input: str | list[dict[str, Any]], instructions: str | None) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if instructions:
            messages.append({"role": "system", "content": instructions})

        if isinstance(input, str):
            messages.append({"role": "user", "content": input})
            return messages

        for item in input:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            content = item.get("content", "")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict) and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                if text_parts:
                    messages.append({"role": role, "content": "\n".join(text_parts)})
        return messages

    def _extract_content(self, message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            if content:
                return content
        elif isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
                else:
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        text_parts.append(text)
            if text_parts:
                return "\n".join(text_parts)

        reasoning = getattr(message, "reasoning", None)
        if isinstance(reasoning, str):
            return reasoning
        reasoning_content = getattr(message, "reasoning_content", None)
        if isinstance(reasoning_content, str):
            return reasoning_content
        return ""

    def _extract_usage(self, response: Any) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        if not usage:
            return {}
        input_tokens = int(self._get_attr(usage, "prompt_tokens", "promptTokens", default=0) or 0)
        output_tokens = int(self._get_attr(usage, "completion_tokens", "completionTokens", default=0) or 0)
        total_tokens = int(self._get_attr(usage, "total_tokens", "totalTokens", default=input_tokens + output_tokens) or (input_tokens + output_tokens))
        result = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

        details = self._get_attr(usage, "prompt_tokens_details", "promptTokensDetails")
        cached = int(self._get_attr(details, "cached_tokens", "cachedTokens", default=0) or 0) if details is not None else 0
        if cached:
            result["cached_input_tokens"] = cached
        return result

    def _get_attr(self, value: Any, *names: str, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, dict):
            for name in names:
                if name in value:
                    return value[name]
            return default
        for name in names:
            attr = getattr(value, name, default)
            if attr is not default:
                return attr
        return default

    def _extract_tool_calls(self, message: Any) -> list[Any] | None:
        tool_calls = self._get_attr(message, "tool_calls", "toolCalls")
        if not tool_calls:
            return None
        result: list[Any] = []
        for tool_call in tool_calls:
            if hasattr(tool_call, "model_dump"):
                result.append(tool_call.model_dump())
            else:
                result.append(tool_call)
        return result

    def _get_default_model(self) -> str:
        return "deepseek/deepseek-v4-flash"

    async def close(self) -> None:
        client_to_close: OpenRouter | None = None
        with self._client_lock:
            if self._client is not None:
                client_to_close = self._client
                self._client = None
        if client_to_close is not None:
            await client_to_close.__aexit__(None, None, None)
