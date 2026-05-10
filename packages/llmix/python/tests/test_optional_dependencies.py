#!/usr/bin/env python3
"""Regression tests for optional extras on the neutral LLMix surface."""

from __future__ import annotations

import builtins
import importlib
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

passed = 0
failed = 0


def assert_true(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def purge_modules(*prefixes: str) -> None:
    """Remove cached modules so imports re-evaluate under patched conditions."""
    for name in list(sys.modules):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(name, None)


@contextmanager
def block_imports(*blocked_names: str):
    """Temporarily raise ImportError for selected module prefixes."""
    original_import = builtins.__import__

    def guarded_import(name: str, globals_dict: Any = None, locals_dict: Any = None, fromlist: Any = (), level: int = 0):
        candidates = [name]
        if fromlist:
            candidates.extend(f"{name}.{item}" for item in fromlist if isinstance(item, str))
        if any(candidate == blocked or candidate.startswith(f"{blocked}.") for candidate in candidates for blocked in blocked_names):
            raise ImportError(f"blocked import for test: {name}")
        return original_import(name, globals_dict, locals_dict, fromlist, level)

    builtins.__import__ = guarded_import
    try:
        yield
    finally:
        builtins.__import__ = original_import


def test_response_cache_degrades_without_redis() -> None:
    purge_modules("llmix.response_cache", "redis")
    with block_imports("redis"):
        cache_module = importlib.import_module("llmix.response_cache")
        cache = cache_module.TwoTierCache("redis-or-memory", redis_url="redis://localhost:6379")
        connected = cache._ensure_redis()
        stats = cache.get_stats()
        cache.close()
        assert_true(connected is False, "response cache does not connect without redis extra")
        assert_true(stats.l2_enabled is True, "response cache keeps L2 intent when redis is requested")
        assert_true(stats.l2_healthy is False, "response cache degrades to L1-only mode without redis extra")


def test_openai_client_imports_without_helicone() -> None:
    # Purge the whole providers package — test_anthropic_client.py installs a stub
    # for llmix.providers at collection time; we need to clear it so importlib
    # can re-load the real package from disk.
    purge_modules("llmix.providers", "lib.telemetry.helicone")
    with block_imports("lib.telemetry.helicone"):
        openai_module = importlib.import_module("llmix.providers.openai_async_client")
        client = openai_module.AsyncOpenAIClient(api_key="test-key")
        assert_true(client.client is not None, "openai client initializes without Helicone module")


def test_dispatchers_import_without_optional_provider_sdk() -> None:
    purge_modules("llmix.dispatchers", "anthropic")
    with block_imports("anthropic"):
        dispatchers_module = importlib.import_module("llmix.dispatchers")
        dispatch = dispatchers_module.openai_dispatch()
        assert_true(callable(dispatch), "dispatcher module imports without optional anthropic SDK")


def test_types_import_without_shared_validation() -> None:
    purge_modules("llmix.types", "lib.infra.validation")
    with block_imports("lib.infra.validation"):
        types_module = importlib.import_module("llmix.types")
        assert_true(types_module.validate_user_id("safe_user-1"), "types falls back to local user validation helper")
        assert_true(not types_module.validate_user_id("../escape"), "types fallback rejects dangerous user ids")


def test_config_import_without_project_root_helpers() -> None:
    purge_modules("llmix.config", "lib.infra.project_root")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text("[project]\nname='llmix-test'\n", encoding="utf-8")
        os.environ["LLMIX_CONFIG_DIR"] = "config/llm"
        previous_cwd = Path.cwd()
        try:
            os.chdir(root)
            with block_imports("lib.infra.project_root"):
                config_module = importlib.import_module("llmix.config")
                resolved = config_module.resolve_config_dir()
            assert_true(
                resolved.config_dir == str((root / "config/llm").resolve()),
                "config falls back to local project-root helpers",
            )
        finally:
            os.chdir(previous_cwd)
            os.environ.pop("LLMIX_CONFIG_DIR", None)


def test_resilience_imports_without_shared_lib() -> None:
    purge_modules("llmix.resilience", "lib.infra.circuit_breaker")
    with block_imports("lib.infra.circuit_breaker"):
        resilience_module = importlib.import_module("llmix.resilience")
        breaker = resilience_module.CircuitBreaker("openai", "")
        breaker.on_failure(status_code=500)
        breaker.on_failure(status_code=500)
        breaker.on_failure(status_code=500)
        assert_true(
            breaker.state == resilience_module.CircuitState.OPEN,
            "resilience module uses the local circuit breaker implementation",
        )


def test_package_import_without_optional_extras() -> None:
    purge_modules("llmix", "lib.telemetry.helicone", "lib.infra.validation", "lib.infra.project_root", "lib.infra.circuit_breaker")
    with block_imports(
        "lib.telemetry.helicone",
        "lib.infra.validation",
        "lib.infra.project_root",
        "lib.infra.circuit_breaker",
    ):
        llmix_module = importlib.import_module("llmix")
        pipeline_cls = llmix_module.CallPipeline
        config_loader = llmix_module.load_mda_config
        registry_manager = llmix_module.ConfigRegistryManager
        registry_publisher = llmix_module.ConfigRegistryPublisher
        assert_true(callable(config_loader), "package exports load_mda_config without shared helpers")
        assert_true(pipeline_cls.__name__ == "CallPipeline", "package exports neutral pipeline surface")
        assert_true(registry_manager.__name__ == "ConfigRegistryManager", "package exports ConfigRegistryManager")
        assert_true(registry_publisher.__name__ == "ConfigRegistryPublisher", "package exports ConfigRegistryPublisher")


def main() -> None:
    test_response_cache_degrades_without_redis()
    test_openai_client_imports_without_helicone()
    test_dispatchers_import_without_optional_provider_sdk()
    test_types_import_without_shared_validation()
    test_config_import_without_project_root_helpers()
    test_resilience_imports_without_shared_lib()
    test_package_import_without_optional_extras()

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
