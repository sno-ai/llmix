"""Centralized environment variable access for lib/llmix.

All os.getenv/os.environ reads for the llmix subsystem live here.
Other modules import from this file instead of reading env vars directly.

API keys are lazy (getter functions) because they may be injected via
constructor params — env is fallback only.
"""

import os

# =============================================================================
# Provider API Keys
# =============================================================================


def get_openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "")


def get_openai_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "")


def get_anthropic_api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY")


def get_gemini_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY")


def get_openrouter_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY")


def get_deepinfra_api_key() -> str | None:
    return os.getenv("DEEPINFRA_API_KEY")


def get_together_api_key() -> str | None:
    return os.getenv("TOGETHER_API_KEY")


def get_novita_api_key() -> str | None:
    return os.getenv("NOVITA_API_KEY")


def get_sno_llm_api_key() -> str | None:
    return os.getenv("SNO_LLM_API_KEY")


# =============================================================================
# GPU / Self-Hosted
# =============================================================================


def get_gpu_base_url() -> str:
    return os.getenv("GPU_BASE_URL", "")


# =============================================================================
# OpenAI GPT-5 Settings
# =============================================================================


def get_openai_gpt5_reasoning_effort() -> str:
    return os.getenv("OPENAI_GPT5_REASONING_EFFORT", "medium")


def get_openai_gpt5_verbosity() -> str:
    return os.getenv("OPENAI_GPT5_VERBOSITY", "low")


# =============================================================================
# Retry Configuration
# =============================================================================


def get_llm_retry_attempts() -> int:
    return int(os.getenv("LLM_RETRY_ATTEMPTS", "6"))


def get_llm_retry_delay_seconds() -> float:
    return float(os.getenv("LLM_RETRY_DELAY_SECONDS", "3.0"))


def get_llm_retry_multiplier() -> float:
    return float(os.getenv("LLM_RETRY_MULTIPLIER", "2.0"))


def get_llm_retry_max_delay() -> float:
    return float(os.getenv("LLM_RETRY_MAX_DELAY", "30.0"))


# =============================================================================
# Environment / Helicone Routing
# =============================================================================


def get_environment() -> str:
    return os.getenv("ENV") or os.getenv("ENVIRONMENT") or "dev"


# =============================================================================
# Client Configuration (DEPRECATED env var fallbacks)
# =============================================================================


def get_capture_telemetry_payload() -> str:
    return os.getenv("LLMIX_CAPTURE_TELEMETRY_PAYLOAD", "")


def get_call_timeout_ms() -> str | None:
    return os.getenv("LLMIX_CALL_TIMEOUT_MS")


def get_config_load_timeout_seconds() -> str | None:
    return os.getenv("LLMIX_CONFIG_LOAD_TIMEOUT_SECONDS")
