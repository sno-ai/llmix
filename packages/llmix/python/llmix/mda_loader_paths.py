"""Path and error helpers for MDA config loading."""

from __future__ import annotations

from pathlib import Path

from snoai_mda_config import MdaConfigError

from llmix.types import (
    ConfigAccessError,
    ConfigNotFoundError,
    InvalidConfigError,
    SecurityError,
    validate_module,
    validate_preset,
)


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
        raise SecurityError(
            f"Path traversal detected: {resolved_path} escapes base directory {base_dir}"
        ) from None



def _reject_legacy_config_path(config_path: str) -> None:
    lower_path = config_path.lower()
    if lower_path.endswith(".yaml") or lower_path.endswith(".yml"):
        raise InvalidConfigError(
            f"Python LLMix presets use .mda files; YAML presets are no longer supported: {config_path}"
        )


def _ensure_mda_config_path(config_path: str) -> None:
    _reject_legacy_config_path(config_path)
    if not config_path.lower().endswith(".mda"):
        raise InvalidConfigError(
            f"Python LLMix presets must use .mda files: {config_path}"
        )


def _map_mda_load_error(exc: Exception, file_path: Path) -> Exception:
    if isinstance(exc, FileNotFoundError):
        return ConfigNotFoundError(f"Config file not found: {file_path}")
    if isinstance(exc, PermissionError):
        return ConfigAccessError(f"Permission denied reading config file: {file_path}")
    if isinstance(exc, MdaConfigError):
        return InvalidConfigError(f"MDA config failed for {file_path}: {exc}")
    return InvalidConfigError(f"MDA config failed for {file_path}: {exc}")


def build_mda_config_file_path(
    config_dir: str | Path, module: str, preset: str
) -> Path:
    """Build the standard MDA config path for a module preset."""
    validate_module(module)
    validate_preset(preset)
    return Path(config_dir).expanduser().resolve() / module / f"{preset}.mda"
