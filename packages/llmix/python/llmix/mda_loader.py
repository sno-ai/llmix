"""MDA config loading and projection utilities for LLMix."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from snoai_mda_config import MdaConfigError, load_mda_source

from llmix.mda_loader_options import MdaConfigLoadOptions
from llmix.mda_loader_paths import (
    _ensure_mda_config_path,
    _map_mda_load_error,
    _reject_legacy_config_path,
    _verify_path_containment,
    build_mda_config_file_path,
)
from llmix.mda_loader_schema import _LLMixMdaPresetSchema, _project_mda_preset_to_config
from llmix.mda_loader_validation import _validate_runtime_config
from llmix.types import LLMConfig, validate_module, validate_preset

__all__ = [
    "MdaConfigLoadOptions",
    "build_mda_config_file_path",
    "load_mda_config",
    "load_mda_config_from_file",
    "load_mda_config_preset",
    "_validate_runtime_config",
    "_verify_path_containment",
]


def load_mda_config(
    path: str | Path, options: MdaConfigLoadOptions | None = None
) -> LLMConfig:
    """Load an explicit LLMix MDA source file."""
    _ensure_mda_config_path(str(path))
    requested_path = Path(path).expanduser()
    _verify_path_containment(requested_path, requested_path.parent)
    file_path = requested_path.resolve()
    try:
        load_options = options or MdaConfigLoadOptions()
        preset = load_mda_source(
            file_path,
            schema=_LLMixMdaPresetSchema,
            verify_integrity=bool(load_options.verify_integrity),
            verify_signatures=bool(load_options.verify_signatures),
            trusted_runtime=bool(load_options.trusted_runtime),
            enforce_requires=bool(load_options.enforce_requires),
            allowed_networks=load_options.allowed_networks,
            trust_policy=load_options.trust_policy,
            rekor_client=load_options.rekor_client,
            sigstore_verifier=load_options.sigstore_verifier,
            did_web_verifier=load_options.did_web_verifier,
        )
    except Exception as exc:
        mapped = _map_mda_load_error(exc, file_path)
        if isinstance(exc, MdaConfigError):
            raise mapped from exc
        raise mapped from None
    return _project_mda_preset_to_config(cast(_LLMixMdaPresetSchema, preset), file_path)


def load_mda_config_preset(
    name: str, base_dir: str | Path, options: MdaConfigLoadOptions | None = None
) -> LLMConfig:
    """
    Load a preset file from ``{base_dir}/{name}.mda``.

    ``name`` may be a bare preset (`"extraction"`) or include a `.mda` suffix.
    """
    _reject_legacy_config_path(name)
    preset_name = name[:-4] if name.lower().endswith(".mda") else name
    validate_preset(preset_name)

    presets_dir = Path(base_dir).expanduser().resolve()
    module_name = presets_dir.name
    validate_module(module_name)

    file_path = presets_dir / f"{preset_name}.mda"
    _verify_path_containment(file_path, presets_dir)
    return load_mda_config(file_path, options)


def load_mda_config_from_file(
    config_dir: str | Path,
    module: str,
    preset: str,
    options: MdaConfigLoadOptions | None = None,
) -> LLMConfig:
    """Load a module preset from the standard MDA config directory layout."""
    file_path = build_mda_config_file_path(config_dir, module, preset)
    _verify_path_containment(file_path, Path(config_dir).expanduser().resolve())
    return load_mda_config(file_path, options)
