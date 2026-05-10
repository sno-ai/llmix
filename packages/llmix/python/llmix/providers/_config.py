"""Standalone provider config helpers.

These helpers intentionally avoid importing ``config.config`` so shared LLM
provider modules can run in isolated containers without pulling in the full
Settings dependency tree.

All env var reads are delegated to ``llmix.env`` — this module is a thin
re-export layer preserving the public API for downstream consumers.
"""

from llmix.env import get_llm_retry_attempts as _get_llm_retry_attempts
from llmix.env import get_llm_retry_delay_seconds as _get_llm_retry_delay_seconds
from llmix.env import get_llm_retry_max_delay as _get_llm_retry_max_delay
from llmix.env import get_llm_retry_multiplier as _get_llm_retry_multiplier
from llmix.env import get_openai_api_key as _get_openai_api_key
from llmix.env import get_openai_base_url as _get_openai_base_url
from llmix.env import get_openai_gpt5_reasoning_effort as _get_openai_gpt5_reasoning_effort
from llmix.env import get_openai_gpt5_verbosity as _get_openai_gpt5_verbosity

DEFAULT_GEMINI_FLASH_MODEL = "gemini-3-flash-preview"
DEFAULT_LLM_TIMEOUT = 300


def get_openai_api_key() -> str:
    """Return the OpenAI API key from the environment."""
    return _get_openai_api_key()


def get_openai_base_url() -> str:
    """Return the OpenAI base URL override from the environment."""
    return _get_openai_base_url()


def get_openai_gpt5_reasoning_effort() -> str:
    """Return the GPT-5 reasoning effort setting."""
    return _get_openai_gpt5_reasoning_effort()


def get_openai_gpt5_verbosity() -> str:
    """Return the GPT-5 verbosity setting."""
    return _get_openai_gpt5_verbosity()


def get_llm_retry_attempts() -> int:
    """Return the max LLM retry attempts."""
    return _get_llm_retry_attempts()


def get_llm_retry_delay_seconds() -> float:
    """Return the initial LLM retry delay in seconds."""
    return _get_llm_retry_delay_seconds()


def get_llm_retry_multiplier() -> float:
    """Return the retry backoff multiplier."""
    return _get_llm_retry_multiplier()


def get_llm_retry_max_delay() -> float:
    """Return the max retry delay in seconds."""
    return _get_llm_retry_max_delay()
