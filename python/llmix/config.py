"""
LLMix Path Configuration Utilities

Provides flexible path resolution with priority:
1. Explicit configDir override (absolute path)
2. Environment variable (LLMIX_CONFIG_DIR) - resolved relative to PROJECT ROOT
3. Default path relative to project root

PROJECT ROOT: Found by walking up from cwd looking for pyproject.toml or package.json

Project root helpers re-exported from lib.infra.project_root (single source of truth).
"""

import os
from pathlib import Path
import json
from dataclasses import dataclass
from typing import Literal

try:
    # Re-export from shared_infra (single source of truth)
    from lib.infra.project_root import LOCKFILES as LOCKFILES
    from lib.infra.project_root import LOCKFILES_PY as LOCKFILES_PY
    from lib.infra.project_root import LOCKFILES_TS as LOCKFILES_TS
    from lib.infra.project_root import find_project_root as find_project_root
    from lib.infra.project_root import has_lockfile as has_lockfile
    from lib.infra.project_root import is_monorepo_root as is_monorepo_root
except ImportError:
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
            if first_pkg_dir is None and (
                (current / "pyproject.toml").exists() or (current / "package.json").exists()
            ):
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
