"""Single source of truth for all LLM provider base URLs.

Every provider client and the Helicone telemetry fallback layer import from here.
NEVER duplicate these URLs elsewhere — update this file only.
"""

from llmix.env import get_gpu_base_url

__all__ = [
    "ANTHROPIC_BASE_URL",
    "DEEPINFRA_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "DIRECT_PROVIDER_URLS",
    "GOOGLE_BASE_URL",
    "GPU_BASE_URL",
    "NOVITA_BASE_URL",
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "TOGETHER_BASE_URL",
]

# OpenAI-compatible providers
OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
TOGETHER_BASE_URL = "https://api.together.xyz/v1"
NOVITA_BASE_URL = "https://api.novita.ai/v3/openai"

# On-prem / self-hosted (no /v1 suffix — gpu_path is appended at call time)
# Environment override: GPU_BASE_URL
#   Dev:  http://gpt1-gpus:8080  (Docker network, internal Caddy HTTP listener)
#   Prod: http://<tailscale-ip>:8080  (Tailscale WireGuard, no Cloudflare 524 timeout)
#   Legacy: https://rt3-llm.sno.ai  (Cloudflare proxy, 120s limit — avoid for >100s calls)
GPU_BASE_URL = get_gpu_base_url()

# Provider name → direct base URL mapping (used by Helicone fallback transport)
DIRECT_PROVIDER_URLS: dict[str, str] = {
    "openai": OPENAI_BASE_URL,
    "anthropic": ANTHROPIC_BASE_URL,
    "google": GOOGLE_BASE_URL,
    "openrouter": OPENROUTER_BASE_URL,
    "deepinfra": DEEPINFRA_BASE_URL,
    "deepseek": DEEPSEEK_BASE_URL,
    "together": TOGETHER_BASE_URL,
    "novita": NOVITA_BASE_URL,
    "snogpu": GPU_BASE_URL,
}
