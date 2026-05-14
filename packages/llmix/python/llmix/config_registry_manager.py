"""Config Registry runtime manager."""

from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from llmix.config_registry_common import (
    _MANIFEST_SCHEMA_VERSION,
    _normalize_sha256_digest,
    _read_json_file,
    _require_manifest_string,
    _sha256_file,
    _compiled_registry_path,
    _compiled_relative_path,
    _from_registry_resolved_config,
    _utcnow,
    _validate_resolved_config,
    _validate_revision,
    _validate_sha256,
)
from llmix.config_registry_root import (
    enforce_registry_root_freshness,
    registry_root_file_digests,
    sorted_registry_root_files,
    verify_registry_root_signatures,
)
from llmix.config_registry_root_parse import parse_registry_root_envelope
from llmix.config_registry_types import (
    REGISTRY_ROOT_FILENAME,
    ConfigRegistryOpenOptions,
    RegistryRootVerificationOptions,
)
from llmix.mda_loader_paths import _verify_path_containment
from llmix.types import (
    ConfigNotFoundError,
    InvalidConfigError,
    SecurityError,
    validate_module,
    validate_preset,
)

logger = logging.getLogger(__name__)


class ConfigRegistryManager:
    """Serve the active resolved configs from immutable compiled revisions."""

    def __init__(
        self, root: str | Path, options: ConfigRegistryOpenOptions | None = None
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.compiled_dir = self.root / "compiled"
        self.current_path = self.root / "current.json"
        self.signed_root_options: RegistryRootVerificationOptions | None = (
            options.signed_root if options else None
        )
        self._lock = threading.RLock()
        self._active_revision: str | None = None
        self._active_manifest_sha256: str | None = None
        self._manifest: dict[str, Any] | None = None
        self._configs: dict[str, dict[str, Any]] = {}
        self._last_reload_error: Exception | None = None
        self._last_successful_reload_at: datetime | None = None
        self._last_reload_failure_at: datetime | None = None

    @classmethod
    def open(
        cls, root: str | Path, options: ConfigRegistryOpenOptions | None = None
    ) -> ConfigRegistryManager:
        manager = cls(root, options)
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
                raise ConfigNotFoundError(
                    f"Preset not found in active Config Registry revision {self.active_revision}: {preset_id}"
                )
            return copy.deepcopy(config)

    def _load_initial_revision(self) -> None:
        pointer = self._read_current_pointer()
        manifest, configs = self._load_revision(pointer)
        with self._lock:
            self._active_revision = pointer["revision"]
            self._active_manifest_sha256 = pointer["manifest_sha256"]
            self._manifest = manifest
            self._configs = configs
            self._last_reload_error = None
            self._last_successful_reload_at = _utcnow()
            self._last_reload_failure_at = None

    def _refresh_if_needed(self) -> None:
        try:
            current_pointer = self._read_current_pointer()
        except Exception as exc:
            self._record_reload_error(exc)
            return

        if (
            current_pointer["revision"] == self._active_revision
            and current_pointer["manifest_sha256"] == self._active_manifest_sha256
        ):
            return

        with self._lock:
            try:
                latest_pointer = self._read_current_pointer()
                if (
                    latest_pointer["revision"] == self._active_revision
                    and latest_pointer["manifest_sha256"]
                    == self._active_manifest_sha256
                ):
                    return

                manifest, configs = self._load_revision(latest_pointer)
                self._active_revision = latest_pointer["revision"]
                self._active_manifest_sha256 = latest_pointer["manifest_sha256"]
                self._manifest = manifest
                self._configs = configs
                self._last_reload_error = None
                self._last_successful_reload_at = _utcnow()
                self._last_reload_failure_at = None
                logger.info(
                    "Config Registry reloaded revision %s", latest_pointer["revision"]
                )
            except Exception as exc:
                self._record_reload_error(exc)

    def _read_current_pointer(self) -> dict[str, str]:
        pointer = _read_json_file(self.current_path)
        revision = pointer.get("revision")
        if not isinstance(revision, str):
            raise InvalidConfigError(
                f"Config Registry pointer is missing string field 'revision': {self.current_path}"
            )
        _validate_revision(revision)
        manifest_sha256 = pointer.get("manifest_sha256")
        if manifest_sha256 is None:
            manifest_sha256 = _sha256_file(
                self.compiled_dir / revision / "manifest.json"
            )
        elif not isinstance(manifest_sha256, str):
            raise InvalidConfigError(
                f"Config Registry pointer is missing string field 'manifest_sha256': {self.current_path}"
            )
        _validate_sha256(manifest_sha256, "Config Registry current manifest_sha256")
        return {"revision": revision, "manifest_sha256": manifest_sha256}

    def _load_revision(
        self, pointer: dict[str, str]
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        revision = pointer["revision"]
        _validate_revision(revision)
        compiled_dir = self.compiled_dir / revision
        if not compiled_dir.exists():
            raise ConfigNotFoundError(
                f"Config Registry compiled revision not found: {compiled_dir}"
            )

        manifest_path = compiled_dir / "manifest.json"
        if _sha256_file(manifest_path) != pointer["manifest_sha256"]:
            raise InvalidConfigError(
                f"Checksum mismatch for Config Registry manifest {manifest_path}"
            )
        manifest = _read_json_file(manifest_path)

        manifest_revision = manifest.get("revision")
        if manifest_revision != revision:
            raise InvalidConfigError(
                f"Config Registry manifest revision mismatch in {manifest_path}"
            )
        if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            raise InvalidConfigError(
                f"Unsupported Config Registry manifest schema version in {manifest_path}"
            )

        presets = manifest.get("presets")
        if not isinstance(presets, dict):
            raise InvalidConfigError(
                f"Config Registry manifest presets index must be an object: {manifest_path}"
            )
        self._verify_signed_registry_root_if_needed(pointer, manifest, compiled_dir)

        configs: dict[str, dict[str, Any]] = {}
        for preset_id, entry in presets.items():
            if not isinstance(preset_id, str) or not isinstance(entry, dict):
                raise InvalidConfigError(
                    f"Invalid Config Registry manifest entry in {manifest_path}"
                )

            _require_manifest_string(entry, "source_path", preset_id)
            source_path = self._resolve_compiled_artifact(
                compiled_dir, entry, "source_path", preset_id
            )
            self._verify_compiled_checksum(
                source_path, entry, "source_sha256", preset_id
            )
            resolved_path = self._resolve_compiled_artifact(
                compiled_dir, entry, "resolved_path", preset_id
            )
            self._verify_compiled_checksum(
                resolved_path, entry, "resolved_sha256", preset_id
            )

            resolved = _read_json_file(resolved_path)
            _validate_resolved_config(resolved_path, resolved)
            configs[preset_id] = _from_registry_resolved_config(resolved)

        return manifest, configs

    def _verify_signed_registry_root_if_needed(
        self, pointer: dict[str, str], manifest: dict[str, Any], compiled_dir: Path
    ) -> None:
        if self.signed_root_options is None:
            return

        root_path = compiled_dir / REGISTRY_ROOT_FILENAME
        if self.signed_root_options.expected_root_digest is not None:
            expected_root_digest = _normalize_sha256_digest(
                self.signed_root_options.expected_root_digest,
                "Config Registry expected_root_digest",
            )
            if _sha256_file(root_path) != expected_root_digest:
                raise SecurityError(
                    "Registry root digest does not match expected_root_digest"
                )
        envelope = parse_registry_root_envelope(_read_json_file(root_path), str(root_path))
        verify_registry_root_signatures(envelope, self.signed_root_options)
        enforce_registry_root_freshness(envelope, self.signed_root_options)
        self._verify_registry_root_payload(envelope["payload"], pointer, manifest, compiled_dir)

    def _verify_registry_root_payload(
        self,
        payload: dict[str, Any],
        pointer: dict[str, str],
        manifest: dict[str, Any],
        compiled_dir: Path,
    ) -> None:
        self._verify_registry_root_bindings(payload, pointer, manifest)
        expected_files = registry_root_file_digests(manifest)
        actual_files = sorted_registry_root_files(payload["files"])
        if actual_files != expected_files:
            raise SecurityError(
                "Registry root file digest set does not match the selected manifest"
            )

        for file in actual_files:
            relative_path = _compiled_relative_path(pointer["revision"], file["path"])
            artifact_path = compiled_dir / relative_path
            _verify_path_containment(artifact_path, compiled_dir)
            if _sha256_file(artifact_path) != file["sha256"]:
                raise SecurityError(f"Registry root file digest mismatch: {file['path']}")

    def _verify_registry_root_bindings(
        self, payload: dict[str, Any], pointer: dict[str, str], manifest: dict[str, Any]
    ) -> None:
        revision = pointer["revision"]
        if payload["revision"] != revision or manifest.get("revision") != revision:
            raise SecurityError(
                "Registry root revision does not match the active current pointer"
            )
        if (
            payload["current"]["revision"] != revision
            or payload["current"]["manifest_sha256"] != pointer["manifest_sha256"]
        ):
            raise SecurityError("Registry root current binding does not match current.json")
        if payload["current"]["sha256"] != _sha256_file(self.current_path):
            raise SecurityError("Registry root current binding digest mismatch")
        if payload["manifest"]["path"] != _compiled_registry_path(
            revision, "manifest.json"
        ):
            raise SecurityError(
                "Registry root manifest path does not match the active compiled revision"
            )
        if payload["manifest"]["sha256"] != pointer["manifest_sha256"]:
            raise SecurityError("Registry root manifest digest does not match current.json")

    def _resolve_compiled_artifact(
        self, compiled_dir: Path, entry: dict[str, Any], key: str, preset_id: str
    ) -> Path:
        relative_path = entry.get(key)
        if not isinstance(relative_path, str):
            raise InvalidConfigError(
                f"Config Registry manifest entry is missing {key}: {preset_id}"
            )
        artifact_path = compiled_dir / relative_path
        _verify_path_containment(artifact_path, compiled_dir)
        return artifact_path

    def _verify_compiled_checksum(
        self, path: Path, entry: dict[str, Any], key: str, preset_id: str
    ) -> None:
        expected = entry.get(key)
        if not isinstance(expected, str):
            raise InvalidConfigError(
                f"Config Registry manifest entry is missing {key}: {preset_id}"
            )
        actual = _sha256_file(path)
        if actual != expected:
            raise InvalidConfigError(
                f"Checksum mismatch for Config Registry artifact {path}"
            )

    def _record_reload_error(self, exc: Exception) -> None:
        self._last_reload_error = exc
        self._last_reload_failure_at = _utcnow()
        logger.error("Config Registry reload failed: %s", exc)
