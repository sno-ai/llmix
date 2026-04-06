"""
Shared Gemini LLM utilities

Provides common functionality for both sync and async Gemini clients:
- Tool calling data structures (OpenAI-compatible)
- Message formatting for Gemini API
- OpenAI ↔ Gemini tool format conversion
- Response text extraction
- Function call extraction
- Retry configuration
"""

import json
import logging
import random
from dataclasses import dataclass
from typing import Any

from llmix.providers._config import get_llm_retry_attempts, get_llm_retry_delay_seconds, get_llm_retry_max_delay, get_llm_retry_multiplier

logger = logging.getLogger(__name__)

try:
    from google.genai import types
except ImportError as exc:
    raise ImportError("The 'google-genai' library is required. Please install it using 'pip install google-genai'.") from exc

# Gemini generation configuration whitelist (aligns with google-genai 0.6+ API)
GEMINI_GENERATION_CONFIG_FIELDS: set[str] = {
    "automatic_function_calling",
    "audio_timestamp",
    "cached_content",
    "candidate_count",
    "image_config",
    "labels",
    "logprobs",
    "max_output_tokens",
    "media_resolution",
    "presence_penalty",
    "response_json_schema",
    "response_logprobs",
    "response_mime_type",
    "response_modalities",
    "response_schema",
    "safety_settings",
    "seed",
    "speech_config",
    "stop_sequences",
    "system_instruction",
    "temperature",
    "thinking_config",
    "tool_config",
    "tools",
    "top_k",
    "top_p",
}


@dataclass
class ToolCallFunction:
    """Simple container for tool call function info"""

    name: str
    arguments: str


@dataclass
class ToolCall:
    """Simple container for tool call info compatible with OpenAI format"""

    id: str
    type: str
    function: ToolCallFunction


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
        raise ValueError("Invalid Gemini retry config values: expected numeric types") from exc

    if attempts < 1:
        raise ValueError(f"Invalid Gemini retry attempts: {attempts} (must be >= 1)")
    if initial_delay <= 0:
        raise ValueError(f"Invalid Gemini initial retry delay: {initial_delay} (must be > 0)")
    if multiplier <= 0:
        raise ValueError(f"Invalid Gemini retry multiplier: {multiplier} (must be > 0)")
    if max_delay < initial_delay:
        raise ValueError(f"Invalid Gemini retry max delay: {max_delay} (must be >= initial_delay {initial_delay})")

    return {"attempts": attempts, "initial_delay": initial_delay, "multiplier": multiplier, "max_delay": max_delay}


def jittered_delay(base_delay: float) -> float:
    """Apply ±50% jitter to retry delay to prevent thundering herd."""
    return base_delay * (0.5 + random.random())


def reformat_messages_for_gemini(messages: list[dict[str, Any]]) -> tuple[str | None, list[types.Content]]:
    """
    Reformat messages for Gemini API

    Args:
        messages: List of messages in OpenAI format

    Returns:
        tuple: (system_instruction, contents_list)

    Notes:
        - System messages aggregated into system_instruction
        - "assistant" role mapped to "model" role
        - Empty contents → adds default user message
        - Conversation must end with user role
    """
    system_instruction: str | None = None
    contents: list[types.Content] = []

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if role == "system":
            system_text = content if isinstance(content, str) else str(content)
            system_instruction = system_text if system_instruction is None else f"{system_instruction}\n\n{system_text}"
            continue

        # LH: Handle string content (primary case for MemU)
        if isinstance(content, str):
            parts = [types.Part.from_text(text=content)]
        elif isinstance(content, list):
            # LH: Handle pre-processed multimodal content
            parts = content
        else:
            logger.warning(f"Unexpected content type in message: {type(content)}")
            parts = [types.Part.from_text(text=str(content))]

        # LH: Map roles for Gemini API
        gemini_role = "user" if role == "user" else "model"
        formatted_content = types.Content(role=gemini_role, parts=parts)
        contents.append(formatted_content)

    # LH: GEMINI FIX - Ensure conversation ends with user role
    if contents and contents[-1].role != "user":
        logger.info("Gemini API requirement: conversations must end with user message - adding continuation message")
        continuation_content = types.Content(
            role="user", parts=[types.Part.from_text(text="Please continue processing based on the above information.")]
        )
        contents.append(continuation_content)

    # LH: INTEGRATION FIX: Validate that contents is not empty - Gemini API requires at least one content item
    if not contents:
        logger.warning("No conversation contents found - adding default user message for Gemini API compatibility")
        # LH: INTEGRATION FIX: Add a default user message when contents is empty
        default_content = types.Content(role="user", parts=[types.Part.from_text(text="Please respond based on the system instruction.")])
        contents = [default_content]

    return system_instruction, contents


def convert_openai_tools_to_gemini(openai_tools: list[dict[str, Any]]) -> list[Any]:
    """
    # LH: INTEGRATION FIX: Convert OpenAI-style tools to Gemini types.Tool format

    Args:
        openai_tools: List of OpenAI-style tool definitions

    Returns:
        List of Gemini types.Tool objects
    """
    gemini_tools: list[Any] = []

    for idx, tool in enumerate(openai_tools):
        if not isinstance(tool, dict):
            raise ValueError(f"Invalid tool at index {idx}: expected dict, got {type(tool).__name__}")

        if tool.get("type") != "function":
            raise ValueError(f"Unsupported tool type at index {idx}: {tool.get('type')!r}")

        func_def = tool.get("function")
        if not isinstance(func_def, dict):
            raise ValueError(f"Invalid function definition at index {idx}: expected dict")

        name = func_def.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid function name at index {idx}: expected non-empty string")

        raw_params = func_def.get("parameters", {})
        if raw_params is None:
            raw_params = {}
        if not isinstance(raw_params, dict):
            raise ValueError(f"Invalid parameters schema for tool '{name}': expected dict")

        function_declaration = types.FunctionDeclaration(name=name, description=func_def.get("description", ""), parameters_json_schema=raw_params)
        gemini_tools.append(types.Tool(function_declarations=[function_declaration]))

    return gemini_tools


def extract_text_from_response(response) -> str:
    """
    # LH: Extract text content from Gemini response following Google's SDK patterns

    CRITICAL: Checks for function calls FIRST to avoid SDK warnings.
    Extracts ALL text parts, not just first.

    Args:
        response: Raw response from Gemini API

    Returns:
        str: Extracted text content
    """
    content_text = None
    parts = None

    # LH: CRITICAL FIX - Check for function calls first (Google's recommended pattern)
    if hasattr(response, "function_calls") and response.function_calls:
        logger.info(f"Response contains {len(response.function_calls)} function calls - using safe text extraction")
        # When response has function calls, extract text from candidates.content.parts directly
        # This avoids the SDK warning about response.text on function call responses

    # LH: Primary parsing path for google-genai response structure
    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and candidate.content is not None:
            parts = getattr(candidate.content, "parts", None)

    # LH: Extract text from parts (collect ALL text parts, not just first)
    if parts:
        text_parts = []
        for part in parts:
            text_value = getattr(part, "text", None)
            if text_value:
                text_parts.append(text_value)

        if text_parts:
            content_text = " ".join(text_parts)
            logger.info(f"Combined {len(text_parts)} text parts for content")

    # LH: CRITICAL FIX - Never use response.text when function calls are present
    # This prevents Google SDK warnings about losing structured data
    if content_text is None:
        # LH: Check if response has function calls - if so, avoid response.text completely
        has_function_calls = hasattr(response, "function_calls") and response.function_calls

        if has_function_calls:
            logger.info("Function call response detected - using safe text extraction")
            # For function call responses, content_text might be empty and that's OK
            # The function calls are the primary content, not text
            content_text = ""
        else:
            # LH: For non-function-call responses, use response.text safely
            if hasattr(response, "text"):
                content_text = response.text
                logger.info("Text extracted from response (no function calls present)")
            elif hasattr(response, "parts"):
                content_text = "".join([part.text for part in response.parts if hasattr(part, "text")])
            else:
                content_text = ""

    return content_text or ""


def extract_function_calls_from_response(response) -> list[ToolCall] | None:
    """
    # LH: Extract function calls from Gemini response and convert to OpenAI format

    Args:
        response: Raw response from Gemini API

    Returns:
        List of OpenAI-format tool calls or None if no function calls
    """
    tool_calls: list[ToolCall] = []

    # LH: Check for function calls in Gemini response
    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and candidate.content is not None:
            parts = getattr(candidate.content, "parts", None)
            if parts:
                for part in parts:
                    # LH: Check if this part is a function call
                    if hasattr(part, "function_call") and part.function_call:
                        func_call = part.function_call

                        # LH: Convert Gemini function call to OpenAI-compatible format
                        tool_call_function = ToolCallFunction(name=func_call.name, arguments=json.dumps(dict(func_call.args), ensure_ascii=False))
                        tool_call = ToolCall(id=f"call_{len(tool_calls)}", type="function", function=tool_call_function)
                        tool_calls.append(tool_call)
                        logger.info(f"Converted Gemini function call: {func_call.name}")

    return tool_calls if tool_calls else None


def strip_additional_properties_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively strip additionalProperties from JSON schema for Gemini API compatibility.

    Gemini API does not support the additionalProperties field that OpenAI requires.
    This function removes it from all nested objects in the schema.

    Args:
        schema: JSON schema dict (potentially with additionalProperties)

    Returns:
        Cleaned JSON schema dict without additionalProperties
    """
    if not isinstance(schema, dict):
        return schema

    # Create a copy to avoid modifying original
    cleaned = dict(schema)

    # Remove additionalProperties if present
    cleaned.pop("additionalProperties", None)

    # Recursively clean nested schemas
    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {key: strip_additional_properties_from_schema(value) for key, value in cleaned["properties"].items()}

    # Handle array items
    if "items" in cleaned:
        cleaned["items"] = strip_additional_properties_from_schema(cleaned["items"])

    # Handle oneOf/anyOf/allOf
    for key in ("oneOf", "anyOf", "allOf"):
        if key in cleaned and isinstance(cleaned[key], list):
            cleaned[key] = [strip_additional_properties_from_schema(item) for item in cleaned[key]]

    return cleaned


def convert_pydantic_to_gemini_schema(pydantic_model) -> dict[str, Any]:  # pylint: disable=import-outside-toplevel
    """
    Convert Pydantic model to Gemini-compatible JSON schema.

    Extracts JSON schema from Pydantic model and strips additionalProperties
    which is not supported by Gemini API.

    Args:
        pydantic_model: Pydantic BaseModel class

    Returns:
        Clean JSON schema dict compatible with Gemini's response_json_schema
    """
    try:
        from pydantic import BaseModel  # pylint: disable=import-outside-toplevel

        if not (isinstance(pydantic_model, type) and issubclass(pydantic_model, BaseModel)):
            raise ValueError(f"{pydantic_model} is not a Pydantic model class")

        # Generate JSON schema from Pydantic model - mypy: ignore due to runtime type check
        raw_schema = pydantic_model.model_json_schema()  # type: ignore[attr-defined]

        # Strip additionalProperties recursively
        cleaned_schema = strip_additional_properties_from_schema(raw_schema)

        logger.info(f"Converted Pydantic model {pydantic_model.__name__} to Gemini-compatible schema")
        return cleaned_schema

    except ImportError as exc:
        logger.error("Pydantic not available - cannot convert model to schema")
        raise ValueError("Pydantic is required for schema conversion") from exc
    except Exception as e:
        logger.error(f"Failed to convert Pydantic model to Gemini schema: {e}")
        raise ValueError(f"Schema conversion failed: {e}") from e
