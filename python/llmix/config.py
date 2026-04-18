"""
LLMix Path Configuration Utilities

Provides flexible path resolution with priority:
1. Explicit configDir override (absolute path)
2. Environment variable (LLMIX_CONFIG_DIR) - resolved relative to PROJECT ROOT
3. Default path relative to project root

PROJECT ROOT: Found by walking up from cwd looking for pyproject.toml or package.json
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from typing import Literal

import yaml

from llmix.types import ConfigAccessError, ConfigNotFoundError, InvalidConfigError, SecurityError, validate_module, validate_preset

# Legacy compatibility mapping reused from the retired config loader.
_CAMEL_TO_SNAKE: dict[str, str] = {
    "baseUrl": "base_url",
    "maxOutputTokens": "max_output_tokens",
    "maxRetries": "max_retries",
    "topP": "top_p",
    "topK": "top_k",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
    "stopSequences": "stop_sequences",
    "totalTime": "total_time",
    "streamFirstChunkTime": "stream_first_chunk_time",
    "providerOptions": "provider_options",
    "bypassGateway": "bypass_gateway",
    "configId": "config_id",
    "enableThinking": "enable_thinking",
    "keepThinkingOutput": "keep_thinking_output",
    "thinkingBudget": "thinking_budget",
    "reasoningEffort": "reasoning_effort",
    "textVerbosity": "text_verbosity",
    "structuredOutputs": "structured_outputs",
    "parallelToolCalls": "parallel_tool_calls",
    "logitBias": "logit_bias",
    "strictJsonSchema": "strict_json_schema",
    "maxCompletionTokens": "max_completion_tokens",
    "serviceTier": "service_tier",
    "promptCacheKey": "prompt_cache_key",
    "promptCacheRetention": "prompt_cache_retention",
    "gpuPath": "gpu_path",
    "maxItems": "max_items",
}

# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------

LOCKFILES_TS = ["bun.lock", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"]
LOCKFILES_PY = ["uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock"]
LOCKFILES = LOCKFILES_TS + LOCKFILES_PY


def is_monorepo_root(directory: Path) -> bool:
    """Check if directory is the monorepo root by looking for workspaces in package.json."""
    pkg_json = directory / "package.json"
    if pkg_json.exists():
        try:
            with open(pkg_json) as f:
                pkg = json.load(f)
                if "workspaces" in pkg:
                    return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def has_lockfile(directory: Path) -> bool:
    """Check if directory contains a lockfile."""
    return any((directory / f).exists() for f in LOCKFILES)


def find_project_root(start_dir: Path | None = None) -> Path:
    """Find project root by walking up directory tree."""
    current = (start_dir or Path.cwd()).resolve()
    first_pkg_dir: Path | None = None
    first_lockfile_dir: Path | None = None

    while current != current.parent:
        if is_monorepo_root(current):
            return current
        if first_lockfile_dir is None and has_lockfile(current):
            first_lockfile_dir = current
        if first_pkg_dir is None and ((current / "pyproject.toml").exists() or (current / "package.json").exists()):
            first_pkg_dir = current
        current = current.parent

    return first_lockfile_dir or first_pkg_dir or Path.cwd()


@dataclass
class LLMixPathConfig:
    """Configuration options for LLM config directory resolution."""

    config_dir: str | None = None
    """Explicit config directory path (highest priority)"""

    env_var: str | None = None
    """Custom environment variable name (default: LLMIX_CONFIG_DIR)"""

    default_path: str | None = None
    """Default path relative to project root (default: ./config/llm)"""

    project_root: str | None = None
    """Project root directory (default: Path.cwd())"""


@dataclass
class ResolvedConfigDir:
    """Result of config directory resolution."""

    config_dir: str
    """Resolved absolute path to config directory"""

    source: Literal["explicit", "env", "default"]
    """How the path was resolved"""


def resolve_config_dir(options: LLMixPathConfig | None = None) -> ResolvedConfigDir:
    """
    Resolve the LLMix config directory path.

    Args:
        options: Optional path configuration overrides

    Returns:
        Resolved absolute path to config directory and source

    Example:
        >>> result = resolve_config_dir()
        >>> result.config_dir
        '/path/to/project/config/llm'
        >>> result.source
        'default'
    """
    env_var_name = options.env_var if options and options.env_var else "LLMIX_CONFIG_DIR"
    default_relative_path = options.default_path if options and options.default_path else "./config/llm"
    project_root = Path(options.project_root) if options and options.project_root else Path.cwd()

    # Priority 1: Explicit override
    if options and options.config_dir:
        return ResolvedConfigDir(config_dir=str(Path(options.config_dir).resolve()), source="explicit")

    # Priority 2: Environment variable - always resolve from project root
    # Dynamic config override — exempt from config module migration
    env_value = os.environ.get(env_var_name)
    if env_value:
        resolved_path = (find_project_root() / env_value).resolve()
        return ResolvedConfigDir(config_dir=str(resolved_path), source="env")

    # Priority 3: Default relative to project root (use find_project_root, not cwd)
    actual_project_root = find_project_root() if project_root == Path.cwd() else project_root
    return ResolvedConfigDir(config_dir=str((actual_project_root / default_relative_path).resolve()), source="default")


def _verify_path_containment(resolved_path: Path, base_dir: Path) -> None:
    """Verify that a resolved file path stays within an allowed base directory."""
    normalized_base = base_dir.resolve()
    normalized_path = resolved_path.resolve()

    try:
        real_base = normalized_base.resolve()
    except (OSError, RuntimeError):
        real_base = normalized_base

    try:
        real_path = normalized_path.resolve()
    except (OSError, RuntimeError):
        real_path = normalized_path

    try:
        real_path.relative_to(real_base)
    except ValueError:
        raise SecurityError(f"Path traversal detected: {resolved_path} escapes base directory {base_dir}") from None


def _load_yaml_file(file_path: Path) -> dict[str, Any]:
    """Load a YAML config file using the same safety/error semantics as the legacy loader."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigNotFoundError(f"Config file not found: {file_path}") from None
    except PermissionError:
        raise ConfigAccessError(f"Permission denied reading config file: {file_path}") from None

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise InvalidConfigError(f"YAML parsing failed for {file_path}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InvalidConfigError(f"Config must be a dictionary, got {type(parsed).__name__}")

    config = _normalize_config_shape(cast(dict[str, Any], parsed))
    if "provider" not in config:
        raise InvalidConfigError(f"Missing required field 'provider' in {file_path}")
    if "model" not in config:
        raise InvalidConfigError(f"Missing required field 'model' in {file_path}")

    return config


def _normalize_config_keys(value: Any) -> Any:
    """Normalize known legacy camelCase keys to the public Python snake_case shape."""
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = raw_key if isinstance(raw_key, str) else str(raw_key)
            normalized_key = _CAMEL_TO_SNAKE.get(key, key)
            normalized[normalized_key] = _normalize_config_keys(item)
        return normalized

    if isinstance(value, list):
        return [_normalize_config_keys(item) for item in value]

    return value


def _normalize_config_shape(config: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize legacy YAML layouts into the public Python config shape.

    Some legacy presets stored provider/model inside ``common``. The public
    loader lifts those fields to the top level and removes them from
    ``common`` so the returned config matches the documented contract.
    """
    normalized = cast(dict[str, Any], _normalize_config_keys(config))
    common_value = normalized.get("common")
    if isinstance(common_value, dict):
        common = dict(common_value)
        provider = common.pop("provider", None)
        model = common.pop("model", None)

        if provider is not None and "provider" not in normalized:
            normalized["provider"] = provider
        if model is not None and "model" not in normalized:
            normalized["model"] = model

        if common:
            normalized["common"] = common
        else:
            normalized.pop("common", None)

    return normalized


def load_config(path: str | Path) -> dict[str, Any]:
    """
    Load a YAML config from an explicit path.

    The public API stays intentionally simple: one file in, one validated config out.
    """
    file_path = Path(path).expanduser().resolve()
    return _load_yaml_file(file_path)


def load_config_preset(name: str, base_dir: str | Path) -> dict[str, Any]:
    """
    Load a preset file from ``{base_dir}/{name}.yaml``.

    ``name`` may be a bare preset (`"extraction"`) or include a `.yaml` suffix.
    """
    preset_name = Path(name).name
    if preset_name.endswith(".yaml"):
        preset_name = preset_name[:-5]
    elif preset_name.endswith(".yml"):
        preset_name = preset_name[:-4]

    validate_preset(preset_name)

    presets_dir = Path(base_dir).expanduser().resolve()
    module_name = presets_dir.name
    validate_module(module_name)

    file_path = presets_dir / f"{preset_name}.yaml"
    _verify_path_containment(file_path, presets_dir)
    return load_config(file_path)
