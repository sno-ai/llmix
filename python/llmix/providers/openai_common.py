"""
OpenAI Client Common Utilities

Shared utilities for both synchronous and asynchronous OpenAI clients:
- Retry configuration and helpers
- Message preprocessing
- GPT-5 parameter handling
- Tool strict mode injection
- Response processing utilities
- Cache optimization helpers
"""

import copy
import logging
import random
from typing import Any

from llmix.providers._config import (
    get_llm_retry_attempts,
    get_llm_retry_delay_seconds,
    get_llm_retry_max_delay,
    get_llm_retry_multiplier,
    get_openai_gpt5_verbosity,
)

logger = logging.getLogger(__name__)

# OpenAI prompt cache minimum token threshold
# Prompts must be >= this many tokens for OpenAI's automatic prompt caching to activate
# See: https://platform.openai.com/docs/guides/prompt-caching
OPENAI_PROMPT_CACHE_MIN_TOKENS = 1024


def build_retry_config() -> dict[str, Any]:
    """
    Build retry configuration from centralized config

    Returns:
        Dictionary with retry parameters (attempts, delays, multiplier)
    """
    try:
        attempts = get_llm_retry_attempts()
        initial_delay = get_llm_retry_delay_seconds()
        multiplier = get_llm_retry_multiplier()
        max_delay = get_llm_retry_max_delay()
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid OpenAI retry config values: expected numeric types") from exc

    if attempts < 1:
        raise ValueError(f"Invalid OpenAI retry attempts: {attempts} (must be >= 1)")
    if initial_delay <= 0:
        raise ValueError(f"Invalid OpenAI initial retry delay: {initial_delay} (must be > 0)")
    if multiplier <= 0:
        raise ValueError(f"Invalid OpenAI retry multiplier: {multiplier} (must be > 0)")
    if max_delay < initial_delay:
        raise ValueError(f"Invalid OpenAI retry max delay: {max_delay} (must be >= initial_delay {initial_delay})")

    return {"attempts": attempts, "initial_delay": initial_delay, "multiplier": multiplier, "max_delay": max_delay}


def jittered_delay(base_delay: float) -> float:
    """Apply ±50% jitter to retry delay to prevent thundering herd.

    Concurrent LLM calls retrying at the same time cause synchronized
    429 storms. Jitter spreads retries so they don't all collide.
    """
    return base_delay * (0.5 + random.random())


def prepare_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """
    Preprocess OpenAI message format

    Args:
        messages: Raw message list

    Returns:
        Processed message list with proper formatting
    """
    processed: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict) and "role" in msg:
            processed_msg: dict[str, Any] = {"role": msg["role"]}

            # Handle content (may be None for tool calls)
            if "content" in msg and msg["content"] is not None:
                processed_msg["content"] = str(msg["content"])
            elif msg["role"] != "assistant" or "tool_calls" not in msg:
                processed_msg["content"] = ""

            # Handle tool calls (for assistant messages)
            if "tool_calls" in msg:
                processed_msg["tool_calls"] = msg["tool_calls"]

            # Handle tool call ID (for tool messages)
            if "tool_call_id" in msg:
                processed_msg["tool_call_id"] = msg["tool_call_id"]
                processed_msg["content"] = str(msg.get("content", ""))

            processed.append(processed_msg)
        else:
            logger.warning("Invalid message format: type=%s keys=%s", type(msg).__name__, list(msg.keys()) if isinstance(msg, dict) else None)

    return processed


def apply_gpt5_chat_parameters(model: str, api_kwargs: dict[str, Any], temperature: float, reasoning_effort: str, max_tokens: int) -> None:
    """
    Apply GPT-5 series specific parameters for Chat Completions API

    Modifies api_kwargs in-place with GPT-5 specific parameters:
    - max_completion_tokens instead of max_tokens
    - temperature from config (should be 1.0)
    - reasoning_effort and verbosity

    Args:
        model: Model name
        api_kwargs: API kwargs dictionary to modify in-place
        temperature: Temperature value from client config
        reasoning_effort: Reasoning effort level
        max_tokens: Max tokens to generate
    """
    if model.startswith("gpt-5"):
        # GPT-5 requires max_completion_tokens instead of max_tokens
        api_kwargs["max_completion_tokens"] = max_tokens
        # GPT-5 requires temperature=1.0 (use config value)
        api_kwargs["temperature"] = temperature
        # Add GPT-5 specific parameters
        api_kwargs["reasoning_effort"] = reasoning_effort
        verbosity = get_openai_gpt5_verbosity()
        api_kwargs["verbosity"] = verbosity

        logger.info(f"OpenAI ({model}) parameters: reasoning_effort={reasoning_effort}, verbosity={verbosity}")
    else:
        # Standard models use max_tokens
        api_kwargs["max_tokens"] = max_tokens


def apply_gpt5_response_parameters(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    model: str, api_kwargs: dict[str, Any], temperature: float, default_temperature: float, reasoning_effort: str, max_output_tokens: int | None
) -> None:
    """
    Apply GPT-5 series specific parameters for Responses API

    Modifies api_kwargs in-place with GPT-5 specific parameters:
    - reasoning object with effort level
    - temperature from config
    - max_output_tokens

    Args:
        model: Model name
        api_kwargs: API kwargs dictionary to modify in-place
        temperature: Temperature override value
        default_temperature: Default temperature from client config
        reasoning_effort: Reasoning effort level
        max_output_tokens: Max output tokens to generate
    """
    if model.startswith("gpt-5"):
        # GPT-5 requires temperature=1.0 (use config value)
        api_kwargs["temperature"] = default_temperature
        if max_output_tokens is not None:
            api_kwargs["max_output_tokens"] = max_output_tokens
        # Response API uses reasoning object (NOT reasoning_effort + verbosity)
        api_kwargs["reasoning"] = {"effort": reasoning_effort}

        logger.info(f"OpenAI Responses ({model}) parameters: reasoning_effort={reasoning_effort}")
    else:
        # Standard models use temperature and max_output_tokens
        api_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            api_kwargs["max_output_tokens"] = max_output_tokens


def inject_strict_mode_chat_tools(tools: list[dict]) -> list[dict]:
    """
    Deep copy tools and inject strict mode for Chat Completions API

    Chat Completions format:
    {"type": "function", "function": {"name": "...", "strict": True}}

    Args:
        tools: Original tools list

    Returns:
        Deep copied tools list with strict mode injected
    """
    tools_list = copy.deepcopy(tools)
    if isinstance(tools_list, list):
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("type") == "function" and "function" in tool and isinstance(tool["function"], dict):
                tool["function"]["strict"] = True
    return tools_list


def inject_strict_mode_response_tools(tools: list[dict]) -> list[dict]:
    """
    Deep copy tools and inject strict mode for Responses API

    Responses API supports both formats:
    - Flat: {"type": "function", "name": "...", "strict": True}
    - Nested: {"type": "function", "function": {"name": "...", "strict": True}}

    Args:
        tools: Original tools list

    Returns:
        Deep copied tools list with strict mode injected
    """
    tools_list = copy.deepcopy(tools)
    if isinstance(tools_list, list):
        for tool in tools_list:
            if isinstance(tool, dict) and tool.get("type") == "function":
                if "function" in tool:
                    # Nested format (backward compatibility)
                    if isinstance(tool["function"], dict):
                        tool["function"]["strict"] = True
                elif "name" in tool:
                    # Flat format (Response API native)
                    tool["strict"] = True
    return tools_list


def extract_output_text(response: Any) -> str | None:
    """
    Extract output text from Responses API response

    Args:
        response: OpenAI Responses API response object

    Returns:
        Extracted text content or None
    """
    if hasattr(response, "output_text"):
        return response.output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            if hasattr(item, "type") and item.type == "message" and hasattr(item, "content"):
                for content_item in item.content:
                    if hasattr(content_item, "text"):
                        return content_item.text
    return None


def process_output_array(response: Any) -> list[dict] | None:
    """
    Process and filter output array for cache optimization

    CRITICAL: Always filters out reasoning items to prevent 404 errors when
    output is later reused as input (reasoning items reference non-existent IDs
    when the original response was not stored).
    Only includes function_call and message items that can be reused as input.

    Args:
        response: OpenAI Responses API response object

    Returns:
        Filtered output array or None
    """
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return None

    output_array = []
    for item in output:
        # Skip reasoning items (they reference non-existent IDs when reused as input)
        if hasattr(item, "type") and item.type == "reasoning":
            continue

        if hasattr(item, "model_dump"):
            # Exclude internal response fields that aren't valid input parameters
            item_dict = item.model_dump(exclude={"status"})
            output_array.append(item_dict)
        else:
            output_array.append(item)

    return output_array


class FunctionCallWrapper:  # pylint: disable=too-few-public-methods
    """
    Wrapper to make Response API function calls compatible with Chat Completions format

    Response API returns function calls with direct attributes (name, arguments).
    This wrapper converts them to Chat Completions format with function object.
    """

    def __init__(self, response_item: Any):
        """
        Initialize wrapper from Response API function call item

        Args:
            response_item: Function call item from Response API output
        """
        self.id = getattr(response_item, "call_id", getattr(response_item, "id", None))
        self.type = "function"
        # Create function object with name and arguments (use descriptive type name for better stack traces)
        self.function = type("FunctionObject", (object,), {"name": response_item.name, "arguments": response_item.arguments})()


def extract_tool_calls_from_response(response: Any) -> list[FunctionCallWrapper] | None:
    """
    Extract function calls from Response API output array

    Args:
        response: OpenAI Responses API response object

    Returns:
        List of FunctionCallWrapper objects or None
    """
    output = getattr(response, "output", None)
    if not isinstance(output, list) or not output:
        return None

    function_call_items = [item for item in output if hasattr(item, "type") and item.type == "function_call"]

    if not function_call_items:
        return None

    return [FunctionCallWrapper(item) for item in function_call_items]


def log_usage_info(model: str, response: Any, attempt: int, api_type: str = "chat") -> None:
    """
    Log token usage information with cache metrics

    Args:
        model: Model name
        response: API response object
        attempt: Attempt number
        api_type: "chat" for Chat Completions, "response" for Responses API
    """
    usage_info = response.usage if hasattr(response, "usage") else None
    if not usage_info:
        return

    prompt_tokens = getattr(usage_info, "prompt_tokens", 0) or getattr(usage_info, "input_tokens", 0) or 0
    completion_tokens = getattr(usage_info, "completion_tokens", 0) or getattr(usage_info, "output_tokens", 0) or 0

    # Try to get cached tokens from different response formats
    # AI SDK v6 uses cache_read_tokens, OpenAI native uses cached_tokens
    cached_tokens = None
    if hasattr(usage_info, "prompt_tokens_details") and usage_info.prompt_tokens_details:
        cached_tokens = getattr(usage_info.prompt_tokens_details, "cache_read_tokens", None)
        if cached_tokens is None:
            cached_tokens = getattr(usage_info.prompt_tokens_details, "cached_tokens", None)
    # Also check input_tokens_details for Responses API
    if cached_tokens is None and hasattr(usage_info, "input_tokens_details") and usage_info.input_tokens_details:
        cached_tokens = getattr(usage_info.input_tokens_details, "cache_read_tokens", None)
        if cached_tokens is None:
            cached_tokens = getattr(usage_info.input_tokens_details, "cached_tokens", None)

    api_label = "Responses" if api_type == "response" else ""
    model_label = f"{api_label} {model}".strip()

    # Log with cache rate percentage
    if cached_tokens is not None and cached_tokens > 0 and prompt_tokens > 0:
        cache_rate = round((cached_tokens / prompt_tokens) * 100)
        logger.info(
            f"OpenAI {model_label} | CACHE HIT | cached={cached_tokens}/{prompt_tokens} ({cache_rate}%) | completion={completion_tokens} tokens"
        )
    elif prompt_tokens >= OPENAI_PROMPT_CACHE_MIN_TOKENS:
        # Log cache miss only for prompts that could be cached (>= OPENAI_PROMPT_CACHE_MIN_TOKENS)
        logger.info(f"OpenAI {model_label} | CACHE MISS | prompt={prompt_tokens} tokens | completion={completion_tokens} tokens")
    else:
        logger.info(f"OpenAI {model_label} | prompt={prompt_tokens} | completion={completion_tokens} | attempt {attempt}")


def extract_usage_dict(response: Any) -> dict[str, Any]:
    """
    Extract usage information as dictionary

    Args:
        response: API response object

    Returns:
        Usage dictionary
    """
    if hasattr(response, "usage") and response.usage:
        return response.usage.model_dump()
    return {}
