"""
LLMix Config Registry

Publishes immutable runtime snapshots from authoring MDA and serves the active
resolved configs through a small runtime manager.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from llmix.mda_loader import MdaConfigLoadOptions, _validate_runtime_config, _verify_path_containment, load_mda_config
from llmix.types import ConfigAccessError, ConfigNotFoundError, InvalidConfigError, SecurityError, validate_module, validate_preset

logger = logging.getLogger(__name__)

_MANIFEST_SCHEMA_VERSION = 1
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PUBLISH_LOCK = threading.RLock()


@dataclass(frozen=True)
class PublishedRevision:
    """Result of a successful registry publish."""

    revision: str
    snapshot_path: Path
    manifest_path: Path
    activated: bool
    preset_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConfigRegistryPublishOptions:
    """Options for publishing authoring MDA into an immutable registry snapshot."""

    verify_integrity: bool = False


@dataclass(frozen=True)
class _PresetSource:
    module: str
    preset: str
    preset_id: str
    authoring_path: Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
    except FileNotFoundError:
        raise ConfigNotFoundError(f"Required registry artifact not found: {path}") from None
    except PermissionError:
        raise ConfigAccessError(f"Permission denied reading registry artifact: {path}") from None
    return digest.hexdigest()


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigNotFoundError(f"Required registry file not found: {path}") from None
    except PermissionError:
        raise ConfigAccessError(f"Permission denied reading registry file: {path}") from None

    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InvalidConfigError(f"Invalid JSON in registry file {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise InvalidConfigError(f"Registry file must contain a JSON object: {path}")

    return value


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _fsync_file(path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_bytes(path, _canonical_json_bytes(value))


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_json(temp_path, value)
        os.replace(temp_path, path)
        _fsync_dir(path.parent)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except (AttributeError, OSError):
        return


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _validate_revision(revision: str) -> None:
    if not revision:
        raise InvalidConfigError("Registry revision cannot be empty")
    if any(char in revision for char in ("/", "\\", "..")):
        raise SecurityError(f"Invalid registry revision: {revision!r}")
    if not _REVISION_PATTERN.match(revision):
        raise InvalidConfigError(f"Invalid registry revision format: {revision!r}")


def _validate_resolved_config(path: Path, value: dict[str, Any]) -> None:
    _validate_runtime_config(value, path)


def _is_legacy_yaml_authoring_path(path: Path) -> bool:
    return path.name.lower().endswith((".yaml", ".yml"))


def _parse_mda_preset_name(path: Path) -> str | None:
    if not path.name.lower().endswith(".mda"):
        return None
    return path.name[:-4]


def _require_manifest_string(entry: dict[str, Any], key: str, preset_id: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise InvalidConfigError(f"Config Registry manifest entry is missing {key}: {preset_id}")
    return value


class ConfigRegistryPublisher:
    """Build immutable registry snapshots from authoring MDA."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.authoring_dir = self.root / "authoring"
        self.snapshots_dir = self.root / "snapshots"
        self.staging_dir = self.snapshots_dir / ".staging"
        self.current_path = self.root / "current.json"

    def publish(
        self,
        *,
        revision: str | None = None,
        activate: bool = True,
        options: ConfigRegistryPublishOptions | None = None,
    ) -> PublishedRevision:
        with _PUBLISH_LOCK:
            return self._publish_locked(revision=revision, activate=activate, options=options)

    def _publish_locked(
        self,
        *,
        revision: str | None,
        activate: bool,
        options: ConfigRegistryPublishOptions | None,
    ) -> PublishedRevision:
        presets = self._discover_presets()
        if not presets:
            raise ConfigNotFoundError(f"No authoring presets found under {self.authoring_dir}")

        published_at = _utcnow()
        revision_id = revision or self._build_revision_id(presets, published_at)
        _validate_revision(revision_id)

        snapshot_dir = self.snapshots_dir / revision_id
        stage_dir = self.staging_dir / f"{revision_id}.tmp"
        manifest_path = snapshot_dir / "manifest.json"

        if snapshot_dir.exists():
            raise InvalidConfigError(f"Registry revision already exists: {revision_id}")

        if stage_dir.exists():
            shutil.rmtree(stage_dir)

        try:
            load_options = MdaConfigLoadOptions(verify_integrity=bool(options.verify_integrity)) if options else None
            manifest = self._build_staged_snapshot(stage_dir, presets, revision_id, published_at, load_options)
            self._verify_staged_snapshot(stage_dir, manifest)
            snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            stage_dir.rename(snapshot_dir)
            _fsync_dir(snapshot_dir.parent)

            if activate:
                _atomic_write_json(self.current_path, {"revision": revision_id})
                logger.info("Config Registry activated revision %s", revision_id)

            logger.info("Config Registry published revision %s", revision_id)
            return PublishedRevision(
                revision=revision_id,
                snapshot_path=snapshot_dir,
                manifest_path=manifest_path,
                activated=activate,
                preset_ids=tuple(sorted(manifest["presets"].keys())),
            )
        except Exception:
            if stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)
            raise

    def _discover_presets(self) -> list[_PresetSource]:
        if not self.authoring_dir.exists():
            raise ConfigNotFoundError(f"Authoring directory not found: {self.authoring_dir}")

        presets: list[_PresetSource] = []
        for module_dir in sorted(self.authoring_dir.iterdir()):
            if not module_dir.is_dir():
                continue
            _verify_path_containment(module_dir, self.authoring_dir)

            module_name = module_dir.name
            validate_module(module_name)

            for path in sorted(module_dir.iterdir()):
                if not path.is_file():
                    continue
                _verify_path_containment(path, self.authoring_dir)

                if _is_legacy_yaml_authoring_path(path):
                    raise InvalidConfigError(f"Legacy YAML authoring presets are no longer supported; use .mda: {path}")

                preset_name = _parse_mda_preset_name(path)
                if preset_name is None:
                    continue

                validate_preset(preset_name)

                presets.append(
                    _PresetSource(
                        module=module_name,
                        preset=preset_name,
                        preset_id=f"{module_name}/{preset_name}",
                        authoring_path=path,
                    )
                )

        return presets

    def _build_revision_id(self, presets: list[_PresetSource], published_at: datetime) -> str:
        digest = hashlib.sha256()
        for preset in presets:
            digest.update(str(preset.authoring_path.relative_to(self.authoring_dir)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(preset.authoring_path.read_bytes())
            digest.update(b"\0")
        timestamp = published_at.strftime("%Y-%m-%dT%H-%M-%SZ")
        return f"{timestamp}_{digest.hexdigest()[:8]}"

    def _build_staged_snapshot(
        self,
        stage_dir: Path,
        presets: list[_PresetSource],
        revision_id: str,
        published_at: datetime,
        load_options: MdaConfigLoadOptions | None,
    ) -> dict[str, Any]:
        manifest_presets: dict[str, Any] = {}
        stage_dir.mkdir(parents=True, exist_ok=True)

        for preset in presets:
            authoring_bytes = preset.authoring_path.read_bytes()
            authoring_rel = Path("authoring") / preset.module / f"{preset.preset}.mda"
            resolved_rel = Path("resolved") / preset.module / f"{preset.preset}.json"

            _write_bytes(stage_dir / authoring_rel, authoring_bytes)
            resolved = load_mda_config(stage_dir / authoring_rel, load_options)
            resolved_dict = cast(dict[str, Any], resolved)
            _validate_resolved_config(stage_dir / authoring_rel, resolved_dict)
            resolved_bytes = _canonical_json_bytes(resolved_dict)
            _write_bytes(stage_dir / resolved_rel, resolved_bytes)

            manifest_presets[preset.preset_id] = {
                "authoring_path": authoring_rel.as_posix(),
                "authoring_sha256": _sha256_bytes(authoring_bytes),
                "resolved_path": resolved_rel.as_posix(),
                "resolved_sha256": _sha256_bytes(resolved_bytes),
            }

        manifest = {
            "revision": revision_id,
            "published_at": published_at.isoformat().replace("+00:00", "Z"),
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "presets": manifest_presets,
        }
        _write_json(stage_dir / "manifest.json", manifest)
        return manifest

    def _verify_staged_snapshot(self, stage_dir: Path, manifest: dict[str, Any]) -> None:
        stored_manifest = _read_json_file(stage_dir / "manifest.json")
        if stored_manifest != manifest:
            raise InvalidConfigError("Staged registry manifest changed during verification")

        presets = manifest.get("presets")
        if not isinstance(presets, dict):
            raise InvalidConfigError("Registry manifest presets index must be an object")

        for preset_id, entry in presets.items():
            if not isinstance(entry, dict):
                raise InvalidConfigError(f"Registry manifest entry must be an object: {preset_id}")

            for sha_key, path_key in (
                ("authoring_sha256", "authoring_path"),
                ("resolved_sha256", "resolved_path"),
            ):
                relative_path = entry.get(path_key)
                expected_sha = entry.get(sha_key)
                if not isinstance(relative_path, str) or not isinstance(expected_sha, str):
                    raise InvalidConfigError(f"Registry manifest entry is missing {path_key} or {sha_key}: {preset_id}")

                artifact_path = stage_dir / relative_path
                _verify_path_containment(artifact_path, stage_dir)
                actual_sha = _sha256_file(artifact_path)
                if actual_sha != expected_sha:
                    raise InvalidConfigError(f"Checksum mismatch for staged registry artifact {artifact_path}")


class ConfigRegistryManager:
    """Serve the active resolved configs from immutable registry snapshots."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.snapshots_dir = self.root / "snapshots"
        self.current_path = self.root / "current.json"
        self._lock = threading.RLock()
        self._active_revision: str | None = None
        self._manifest: dict[str, Any] | None = None
        self._configs: dict[str, dict[str, Any]] = {}
        self._last_reload_error: Exception | None = None
        self._last_successful_reload_at: datetime | None = None
        self._last_reload_failure_at: datetime | None = None

    @classmethod
    def open(cls, root: str | Path) -> ConfigRegistryManager:
        manager = cls(root)
        manager._load_initial_revision()
        return manager

    @property
    def active_revision(self) -> str:
        if self._active_revision is None:
            raise InvalidConfigError("Config Registry manager is not initialized")
        return self._active_revision

    @property
    def last_reload_error(self) -> Exception | None:
        return self._last_reload_error

    @property
    def last_successful_reload_at(self) -> datetime | None:
        return self._last_successful_reload_at

    @property
    def last_reload_failure_at(self) -> datetime | None:
        return self._last_reload_failure_at

    def available_presets(self) -> tuple[str, ...]:
        self._refresh_if_needed()
        with self._lock:
            return tuple(sorted(self._configs.keys()))

    def get_preset(self, module: str, preset: str) -> dict[str, Any]:
        validate_module(module)
        validate_preset(preset)

        self._refresh_if_needed()
        preset_id = f"{module}/{preset}"

        with self._lock:
            config = self._configs.get(preset_id)
            if config is None:
                raise ConfigNotFoundError(f"Preset not found in active Config Registry revision {self.active_revision}: {preset_id}")
            return copy.deepcopy(config)

    def _load_initial_revision(self) -> None:
        revision = self._read_current_revision()
        manifest, configs = self._load_revision(revision)
        with self._lock:
            self._active_revision = revision
            self._manifest = manifest
            self._configs = configs
            self._last_reload_error = None
            self._last_successful_reload_at = _utcnow()
            self._last_reload_failure_at = None

    def _refresh_if_needed(self) -> None:
        try:
            current_revision = self._read_current_revision()
        except Exception as exc:
            self._record_reload_error(exc)
            return

        if current_revision == self._active_revision:
            return

        with self._lock:
            try:
                latest_revision = self._read_current_revision()
                if latest_revision == self._active_revision:
                    return

                manifest, configs = self._load_revision(latest_revision)
                self._active_revision = latest_revision
                self._manifest = manifest
                self._configs = configs
                self._last_reload_error = None
                self._last_successful_reload_at = _utcnow()
                self._last_reload_failure_at = None
                logger.info("Config Registry reloaded revision %s", latest_revision)
            except Exception as exc:
                self._record_reload_error(exc)

    def _read_current_revision(self) -> str:
        pointer = _read_json_file(self.current_path)
        revision = pointer.get("revision")
        if not isinstance(revision, str):
            raise InvalidConfigError(f"Config Registry pointer is missing string field 'revision': {self.current_path}")
        _validate_revision(revision)
        return revision

    def _load_revision(self, revision: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        _validate_revision(revision)
        snapshot_dir = self.snapshots_dir / revision
        if not snapshot_dir.exists():
            raise ConfigNotFoundError(f"Config Registry snapshot not found: {snapshot_dir}")

        manifest_path = snapshot_dir / "manifest.json"
        manifest = _read_json_file(manifest_path)

        manifest_revision = manifest.get("revision")
        if manifest_revision != revision:
            raise InvalidConfigError(f"Config Registry manifest revision mismatch in {manifest_path}")
        if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            raise InvalidConfigError(f"Unsupported Config Registry manifest schema version in {manifest_path}")

        presets = manifest.get("presets")
        if not isinstance(presets, dict):
            raise InvalidConfigError(f"Config Registry manifest presets index must be an object: {manifest_path}")

        configs: dict[str, dict[str, Any]] = {}
        for preset_id, entry in presets.items():
            if not isinstance(preset_id, str) or not isinstance(entry, dict):
                raise InvalidConfigError(f"Invalid Config Registry manifest entry in {manifest_path}")

            _require_manifest_string(entry, "authoring_path", preset_id)
            _require_manifest_string(entry, "authoring_sha256", preset_id)
            resolved_path = self._resolve_snapshot_artifact(snapshot_dir, entry, "resolved_path", preset_id)
            self._verify_snapshot_checksum(resolved_path, entry, "resolved_sha256", preset_id)

            resolved = _read_json_file(resolved_path)
            _validate_resolved_config(resolved_path, resolved)
            configs[preset_id] = resolved

        return manifest, configs

    def _resolve_snapshot_artifact(self, snapshot_dir: Path, entry: dict[str, Any], key: str, preset_id: str) -> Path:
        relative_path = entry.get(key)
        if not isinstance(relative_path, str):
            raise InvalidConfigError(f"Config Registry manifest entry is missing {key}: {preset_id}")
        artifact_path = snapshot_dir / relative_path
        _verify_path_containment(artifact_path, snapshot_dir)
        return artifact_path

    def _verify_snapshot_checksum(self, path: Path, entry: dict[str, Any], key: str, preset_id: str) -> None:
        expected = entry.get(key)
        if not isinstance(expected, str):
            raise InvalidConfigError(f"Config Registry manifest entry is missing {key}: {preset_id}")
        actual = _sha256_file(path)
        if actual != expected:
            raise InvalidConfigError(f"Checksum mismatch for Config Registry artifact {path}")

    def _record_reload_error(self, exc: Exception) -> None:
        self._last_reload_error = exc
        self._last_reload_failure_at = _utcnow()
        logger.error("Config Registry reload failed: %s", exc)
