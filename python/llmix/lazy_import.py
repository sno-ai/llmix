"""
Lazy Import Utility for LLMix

Defers heavy SDK imports (openai, anthropic, google-genai, httpx) to first use.
Produces clear error messages when an optional SDK is not installed.

Usage:
    from llmix.lazy_import import lazy_import

    openai = lazy_import("openai", package_name="openai", install_cmd="uv add openai")

    # First attribute access triggers the real import
    client = openai.AsyncOpenAI(...)
"""

from __future__ import annotations

import importlib
import threading
from types import ModuleType
from typing import Any

# Provider SDK install instructions
PROVIDER_INSTALL_COMMANDS: dict[str, tuple[str, str]] = {
    # module_name -> (package_name, install_cmd)
    "openai": ("openai", "uv add openai"),
    "anthropic": ("anthropic", "uv add anthropic"),
    "google.genai": ("google-genai", "uv add google-genai"),
    "google": ("google-genai", "uv add google-genai"),
    "httpx": ("httpx", "uv add httpx"),
}


class _LazyModule:
    """Proxy that defers import until first attribute access.

    Thread-safe: concurrent first accesses block on a lock and only one
    thread performs the actual import.
    """

    __slots__ = ("_module_name", "_package_name", "_install_cmd", "_module", "_lock")

    def __init__(self, module_name: str, package_name: str, install_cmd: str) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_package_name", package_name)
        object.__setattr__(self, "_install_cmd", install_cmd)
        object.__setattr__(self, "_module", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _resolve(self) -> ModuleType:
        mod = object.__getattribute__(self, "_module")
        if mod is not None:
            return mod
        lock: threading.Lock = object.__getattribute__(self, "_lock")
        with lock:
            # Double-check after acquiring lock
            mod = object.__getattribute__(self, "_module")
            if mod is not None:
                return mod
            module_name: str = object.__getattribute__(self, "_module_name")
            package_name: str = object.__getattribute__(self, "_package_name")
            install_cmd: str = object.__getattribute__(self, "_install_cmd")
            # Distinguish "not installed" from "installed but broken import"
            spec = importlib.util.find_spec(module_name)
            if spec is None:
                raise ImportError(
                    f'"{package_name}" is required. Install with: {install_cmd}'
                )
            # Package is installed; if import fails, re-raise the original error
            # so nested ImportErrors (e.g. missing C extensions) aren't swallowed
            mod = importlib.import_module(module_name)
            object.__setattr__(self, "_module", mod)
            return mod

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __repr__(self) -> str:
        mod = object.__getattribute__(self, "_module")
        module_name: str = object.__getattribute__(self, "_module_name")
        if mod is not None:
            return repr(mod)
        return f"<lazy module {module_name!r} (not yet loaded)>"


def lazy_import(
    module_name: str,
    *,
    package_name: str | None = None,
    install_cmd: str | None = None,
) -> Any:
    """Create a lazy proxy for a module import.

    The real import is deferred until the first attribute access on the
    returned object. If the import fails, an ``ImportError`` with a clear
    install instruction is raised.

    Args:
        module_name: Dotted Python module path (e.g. ``"openai"``).
        package_name: Human-readable package name for error messages.
            Defaults to *module_name*.
        install_cmd: Shell command shown in the error message.
            Falls back to ``PROVIDER_INSTALL_COMMANDS`` lookup, then
            ``"uv add {package_name}"``.

    Returns:
        A proxy object that behaves like the real module on attribute access.
    """
    if package_name is None:
        info = PROVIDER_INSTALL_COMMANDS.get(module_name)
        package_name = info[0] if info else module_name

    if install_cmd is None:
        info = PROVIDER_INSTALL_COMMANDS.get(module_name)
        install_cmd = info[1] if info else f"uv add {package_name}"

    return _LazyModule(module_name, package_name, install_cmd)
