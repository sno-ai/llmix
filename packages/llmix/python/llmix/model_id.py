"""
Model ID normalization.

Gateways such as OpenRouter address a model as ``<vendor>/<model>``
(``openai/gpt-5.6-luna``, ``z-ai/glm-5.2``, ``deepseek/deepseek-v4-flash-0731``).
Every capability rule and pricing key in this package is written against the
bare model name, so the vendor segment has to come off before any lookup.

This is the single place that knows about the vendor segment. Callers that
enumerate individual vendor prefixes are duplicating it.

Mirrors packages/llmix/typescript/src/model-id.ts.
"""

__all__ = ["strip_vendor_prefix"]


def strip_vendor_prefix(model_id: str) -> str:
    """Strip a gateway vendor prefix and lowercase.

    ``openai/gpt-5.6-luna`` -> ``gpt-5.6-luna``
    ``models/gemini-3-flash-preview`` -> ``gemini-3-flash-preview``
    ``gpt-5-mini`` -> ``gpt-5-mini``

    The last ``/`` wins, so a nested path resolves to its final segment.
    """
    lower = model_id.lower()
    _, separator, tail = lower.rpartition("/")
    return tail if separator else lower
