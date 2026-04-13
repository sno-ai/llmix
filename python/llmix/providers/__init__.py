from pathlib import Path
from pkgutil import extend_path
from typing import TYPE_CHECKING

__path__ = extend_path(__path__, __name__)
_GEN1_PKG_DIR = Path(__file__).resolve().parents[3] / '.gen1-reference' / 'lib' / 'llmix' / 'providers'
if _GEN1_PKG_DIR.is_dir():
    gen1_pkg_path = str(_GEN1_PKG_DIR)
    if gen1_pkg_path not in __path__:
        __path__.append(gen1_pkg_path)

"""LLMix provider implementations.

Providers are lazily imported to avoid hard failures when optional
dependencies (e.g. google-genai) are not installed.  Only the provider
actually used at runtime needs its dependencies present.
"""

# Eagerly import only the universal base types (no optional deps).
from llmix.providers.base import BaseLLMClient, LLMResponse

# Lazy-loaded provider classes and factory functions — imported on first access via __getattr__.
_LAZY_IMPORTS: dict[str, str] = {
    'AsyncAnthropicClient': 'llmix.providers.anthropic_client',
    'DeepInfraClient': 'llmix.providers.deepinfra_client',
    'AsyncGeminiClient': 'llmix.providers.gemini_async_client',
    'NovitaClient': 'llmix.providers.novita_client',
    'AsyncOpenAIClient': 'llmix.providers.openai_async_client',
    'GpuClient': 'llmix.providers.onprem_gpu_client',
    'SnoGpuClient': 'llmix.providers.onprem_gpu_client',
    'TogetherClient': 'llmix.providers.together_client',
    'get_async_openai_client': 'llmix.providers.client_factory',
    'reset_async_openai_client': 'llmix.providers.client_factory',
}

if TYPE_CHECKING:
    from llmix.providers.anthropic_client import AsyncAnthropicClient as AsyncAnthropicClient
    from llmix.providers.client_factory import get_async_openai_client as get_async_openai_client
    from llmix.providers.client_factory import reset_async_openai_client as reset_async_openai_client
    from llmix.providers.deepinfra_client import DeepInfraClient as DeepInfraClient
    from llmix.providers.gemini_async_client import AsyncGeminiClient as AsyncGeminiClient
    from llmix.providers.novita_client import NovitaClient as NovitaClient
    from llmix.providers.onprem_gpu_client import GpuClient as GpuClient
    from llmix.providers.onprem_gpu_client import SnoGpuClient as SnoGpuClient
    from llmix.providers.openai_async_client import AsyncOpenAIClient as AsyncOpenAIClient
    from llmix.providers.together_client import TogetherClient as TogetherClient


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(_LAZY_IMPORTS[name])
        attr = getattr(module, name)
        globals()[name] = attr  # Cache for subsequent access
        return attr
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


__all__ = [
    'AsyncAnthropicClient',
    'DeepInfraClient',
    'AsyncGeminiClient',
    'NovitaClient',
    'AsyncOpenAIClient',
    'BaseLLMClient',
    'GpuClient',
    'LLMResponse',
    'SnoGpuClient',
    'TogetherClient',
    'get_async_openai_client',
    'reset_async_openai_client',
]
