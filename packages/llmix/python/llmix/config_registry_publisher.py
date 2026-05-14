"""Config Registry publisher."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Any, cast

from llmix.config_registry_common import (
    _MANIFEST_SCHEMA_VERSION,
    _atomic_write_json,
    _canonical_json_bytes,
    _fsync_dir,
    _is_legacy_yaml_source_path,
    _parse_mda_preset_name,
    _read_json_file,
    _sha256_bytes,
    _sha256_file,
    _to_registry_resolved_config,
    _utcnow,
    _validate_resolved_config,
    _validate_revision,
    _write_bytes,
    _write_json,
)
from llmix.config_registry_root import (
    _registry_root_signing_input_for_envelope,
    build_registry_root_payload,
    create_registry_root_envelope,
    sorted_registry_root_files,
)
from llmix.config_registry_root_parse import (
    parse_registry_root_envelope,
)
from llmix.config_registry_types import (
    REGISTRY_ROOT_FILENAME,
    ConfigRegistryPublishOptions,
    PublishedRevision,
    RegistryRootSigningOptions,
    _PresetSource,
)
from llmix.mda_loader import MdaConfigLoadOptions
from llmix.mda_loader_paths import _verify_path_containment
from llmix.types import ConfigNotFoundError, InvalidConfigError, validate_module, validate_preset

logger = logging.getLogger(__name__)
_PUBLISH_LOCK = threading.RLock()
_STAGING_COUNTER = count()


def _load_mda_config(path: Path, options: MdaConfigLoadOptions | None) -> dict[str, Any]:
    import llmix.config_registry as registry_facade

    return cast(dict[str, Any], registry_facade.load_mda_config(path, options))


def _normalized_registry_root_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["current"] = dict(payload["current"])
    normalized["manifest"] = dict(payload["manifest"])
    normalized["files"] = sorted_registry_root_files(payload["files"])
    return normalized


class ConfigRegistryPublisher:
    """Build immutable registry compiled revisions from source MDA."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.source_dir = self.root / "source"
        self.compiled_dir = self.root / "compiled"
        self.staging_dir = self.compiled_dir / ".staging"
        self.current_path = self.root / "current.json"

    def publish(
        self,
        *,
        revision: str | None = None,
        activate: bool = True,
        options: ConfigRegistryPublishOptions | None = None,
    ) -> PublishedRevision:
        with _PUBLISH_LOCK:
            return self._publish_locked(
                revision=revision, activate=activate, options=options
            )

    def _publish_locked(
        self,
        *,
        revision: str | None,
        activate: bool,
        options: ConfigRegistryPublishOptions | None,
    ) -> PublishedRevision:
        presets = self._discover_presets()
        if not presets:
            raise ConfigNotFoundError(
                f"No source presets found under {self.source_dir}"
            )

        published_at = _utcnow()
        revision_id = revision or self._build_revision_id(presets, published_at)
        _validate_revision(revision_id)

        compiled_dir = self.compiled_dir / revision_id
        stage_dir = self._staging_attempt_path(revision_id, published_at)
        manifest_path = compiled_dir / "manifest.json"

        try:
            load_options = (
                MdaConfigLoadOptions(
                    verify_integrity=bool(options.verify_integrity),
                    verify_signatures=bool(options.verify_signatures),
                    trusted_runtime=bool(options.trusted_runtime),
                    enforce_requires=bool(options.enforce_requires),
                    allowed_networks=options.allowed_networks,
                    trust_policy=options.trust_policy,
                    rekor_client=options.rekor_client,
                    sigstore_verifier=options.sigstore_verifier,
                    did_web_verifier=options.did_web_verifier,
                )
                if options
                else None
            )
            manifest = self._build_staged_revision(
                stage_dir, presets, revision_id, published_at, load_options
            )
            self._verify_staged_revision(stage_dir, manifest)
            manifest_sha256 = _sha256_file(stage_dir / "manifest.json")
            registry_root_sha256 = self._write_registry_root_if_requested(
                stage_dir, manifest, manifest_sha256, options
            )
            committed_manifest_sha256, committed_registry_root_sha256 = (
                self._commit_revision(
                    stage_dir=stage_dir,
                    compiled_dir=compiled_dir,
                    expected_manifest=manifest,
                    manifest_sha256=manifest_sha256,
                    registry_root_sha256=registry_root_sha256,
                    registry_root_options=(
                        options.registry_root if options is not None else None
                    ),
                )
            )

            if activate:
                _atomic_write_json(
                    self.current_path,
                    {
                        "revision": revision_id,
                        "manifest_sha256": committed_manifest_sha256,
                    },
                )
                logger.info("Config Registry activated revision %s", revision_id)

            logger.info("Config Registry published revision %s", revision_id)
            return PublishedRevision(
                revision=revision_id,
                compiled_path=compiled_dir,
                manifest_path=manifest_path,
                manifest_sha256=committed_manifest_sha256,
                activated=activate,
                preset_ids=tuple(sorted(manifest["presets"].keys())),
                registry_root_path=(
                    compiled_dir / REGISTRY_ROOT_FILENAME
                    if committed_registry_root_sha256 is not None
                    else None
                ),
                registry_root_sha256=committed_registry_root_sha256,
            )
        except Exception:
            if stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)
            raise

    def _write_registry_root_if_requested(
        self,
        stage_dir: Path,
        manifest: dict[str, Any],
        manifest_sha256: str,
        options: ConfigRegistryPublishOptions | None,
    ) -> str | None:
        if options is None or options.registry_root is None:
            return None

        payload = build_registry_root_payload(manifest, manifest_sha256)
        envelope = create_registry_root_envelope(payload, options.registry_root)
        root_path = stage_dir / REGISTRY_ROOT_FILENAME
        _write_json(root_path, envelope)
        return _sha256_file(root_path)

    def _commit_revision(
        self,
        *,
        stage_dir: Path,
        compiled_dir: Path,
        expected_manifest: dict[str, Any],
        manifest_sha256: str,
        registry_root_sha256: str | None,
        registry_root_options: RegistryRootSigningOptions | None,
    ) -> tuple[str, str | None]:
        compiled_dir.parent.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            stage_dir.rename(compiled_dir)
            _fsync_dir(compiled_dir.parent)
            return manifest_sha256, registry_root_sha256
        except OSError:
            if not compiled_dir.exists():
                raise

        committed = self._load_matching_existing_revision(
            compiled_dir=compiled_dir,
            expected_manifest=expected_manifest,
            registry_root_sha256=registry_root_sha256,
            registry_root_options=registry_root_options,
        )
        shutil.rmtree(stage_dir, ignore_errors=True)
        return committed

    def _load_matching_existing_revision(
        self,
        *,
        compiled_dir: Path,
        expected_manifest: dict[str, Any],
        registry_root_sha256: str | None,
        registry_root_options: RegistryRootSigningOptions | None,
    ) -> tuple[str, str | None]:
        manifest_path = compiled_dir / "manifest.json"
        existing_manifest = _read_json_file(manifest_path)
        self._verify_staged_revision(compiled_dir, existing_manifest)

        if (
            existing_manifest.get("revision") != expected_manifest.get("revision")
            or existing_manifest.get("schema_version")
            != expected_manifest.get("schema_version")
            or existing_manifest.get("presets") != expected_manifest.get("presets")
        ):
            raise InvalidConfigError(
                f"Registry revision already exists with different contents: {expected_manifest.get('revision')}"
            )

        existing_manifest_sha256 = _sha256_file(manifest_path)
        root_path = compiled_dir / REGISTRY_ROOT_FILENAME
        existing_registry_root_sha256 = (
            _sha256_file(root_path) if root_path.exists() else None
        )
        # Signed roots include publish-time material; same-revision retries must
        # reuse the already committed root once the manifest contents match.
        if registry_root_sha256 is not None:
            if existing_registry_root_sha256 is None:
                raise InvalidConfigError(
                    "Registry revision already exists without the requested registry root"
                )
            if registry_root_options is None:
                raise InvalidConfigError(
                    "Registry revision already exists but no registry root signer is available"
                )
            self._verify_existing_registry_root(
                root_path,
                existing_manifest,
                existing_manifest_sha256,
                registry_root_options,
            )
        return existing_manifest_sha256, existing_registry_root_sha256

    def _staging_attempt_path(self, revision_id: str, published_at: datetime) -> Path:
        stamp = published_at.strftime("%Y%m%dT%H%M%S%fZ")
        counter = next(_STAGING_COUNTER)
        return self.staging_dir / f"{revision_id}.{stamp}.{os.getpid()}.{counter}.tmp"

    def _verify_existing_registry_root(
        self,
        root_path: Path,
        manifest: dict[str, Any],
        manifest_sha256: str,
        registry_root_options: RegistryRootSigningOptions,
    ) -> None:
        envelope = parse_registry_root_envelope(
            _read_json_file(root_path), str(root_path)
        )
        signing_input = _registry_root_signing_input_for_envelope(envelope)
        for signature in envelope["signatures"]:
            if signature["payload-digest"] != signing_input.integrity["digest"]:
                raise InvalidConfigError(
                    "Registry revision already exists with a registry root signature payload mismatch"
                )

        actual_payload = _normalized_registry_root_payload(envelope["payload"])
        expected_payload = _normalized_registry_root_payload(
            build_registry_root_payload(manifest, manifest_sha256)
        )
        if actual_payload != expected_payload:
            raise InvalidConfigError(
                "Registry revision already exists with a registry root that does not match the committed manifest"
            )
        expected_envelope = create_registry_root_envelope(
            expected_payload, registry_root_options
        )
        if (
            envelope["integrity"] != expected_envelope["integrity"]
            or envelope["payload_sha256"] != expected_envelope["payload_sha256"]
            or envelope["signatures"] != expected_envelope["signatures"]
        ):
            raise InvalidConfigError(
                "Registry revision already exists with a registry root signature mismatch"
            )

    def _discover_presets(self) -> list[_PresetSource]:
        if not self.source_dir.exists():
            raise ConfigNotFoundError(
                f"Source directory not found: {self.source_dir}"
            )

        presets: list[_PresetSource] = []
        for module_dir in sorted(self.source_dir.iterdir()):
            if not module_dir.is_dir():
                continue
            _verify_path_containment(module_dir, self.source_dir)

            module_name = module_dir.name
            validate_module(module_name)

            for path in sorted(module_dir.iterdir()):
                if not path.is_file():
                    continue
                _verify_path_containment(path, self.source_dir)

                if _is_legacy_yaml_source_path(path):
                    raise InvalidConfigError(
                        f"Legacy YAML source presets are no longer supported; use .mda: {path}"
                    )

                preset_name = _parse_mda_preset_name(path)
                if preset_name is None:
                    continue

                validate_preset(preset_name)

                presets.append(
                    _PresetSource(
                        module=module_name,
                        preset=preset_name,
                        preset_id=f"{module_name}/{preset_name}",
                        source_path=path,
                    )
                )

        return presets

    def _build_revision_id(
        self, presets: list[_PresetSource], published_at: datetime
    ) -> str:
        digest = hashlib.sha256()
        for preset in presets:
            digest.update(
                str(preset.source_path.relative_to(self.source_dir)).encode(
                    "utf-8"
                )
            )
            digest.update(b"\0")
            digest.update(preset.source_path.read_bytes())
            digest.update(b"\0")
        timestamp = published_at.strftime("%Y-%m-%dT%H-%M-%SZ")
        return f"{timestamp}_{digest.hexdigest()[:8]}"

    def _build_staged_revision(
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
            source_bytes = preset.source_path.read_bytes()
            source_rel = Path("source") / preset.module / f"{preset.preset}.mda"
            resolved_rel = Path("resolved") / preset.module / f"{preset.preset}.json"

            _write_bytes(stage_dir / source_rel, source_bytes)
            resolved_dict = _load_mda_config(stage_dir / source_rel, load_options)
            _validate_resolved_config(stage_dir / source_rel, resolved_dict)
            registry_resolved = _to_registry_resolved_config(resolved_dict)
            resolved_bytes = _canonical_json_bytes(registry_resolved)
            _write_bytes(stage_dir / resolved_rel, resolved_bytes)

            manifest_presets[preset.preset_id] = {
                "source_path": source_rel.as_posix(),
                "source_sha256": _sha256_bytes(source_bytes),
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

    def _verify_staged_revision(
        self, stage_dir: Path, manifest: dict[str, Any]
    ) -> None:
        stored_manifest = _read_json_file(stage_dir / "manifest.json")
        if stored_manifest != manifest:
            raise InvalidConfigError(
                "Staged registry manifest changed during verification"
            )

        presets = manifest.get("presets")
        if not isinstance(presets, dict):
            raise InvalidConfigError(
                "Registry manifest presets index must be an object"
            )

        for preset_id, entry in presets.items():
            if not isinstance(entry, dict):
                raise InvalidConfigError(
                    f"Registry manifest entry must be an object: {preset_id}"
                )

            for sha_key, path_key in (
                ("source_sha256", "source_path"),
                ("resolved_sha256", "resolved_path"),
            ):
                relative_path = entry.get(path_key)
                expected_sha = entry.get(sha_key)
                if not isinstance(relative_path, str) or not isinstance(
                    expected_sha, str
                ):
                    raise InvalidConfigError(
                        f"Registry manifest entry is missing {path_key} or {sha_key}: {preset_id}"
                    )

                artifact_path = stage_dir / relative_path
                _verify_path_containment(artifact_path, stage_dir)
                actual_sha = _sha256_file(artifact_path)
                if actual_sha != expected_sha:
                    raise InvalidConfigError(
                        f"Checksum mismatch for staged registry artifact {artifact_path}"
                    )
