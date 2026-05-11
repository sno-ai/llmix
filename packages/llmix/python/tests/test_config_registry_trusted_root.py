#!/usr/bin/env python3
"""Signed registry-root coverage for the Python Config Registry."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from snoai_mda_config import DEFAULT_PAYLOAD_TYPE, DidWebVerificationInput
from snoai_mda_config.integrity import canonicalize_artifact, hash_canonical

from llmix import (
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    ConfigRegistryPublishOptions,
    ConfigRegistryPublisher,
    RegistryRootSigningInput,
    RegistryRootSigningOptions,
    RegistryRootVerificationOptions,
    load_llmix_trust_manifest,
    registry_root_options_from_trust_manifest,
)
from llmix.config_registry_types import REGISTRY_ROOT_PAYLOAD_TYPE
from llmix.config_registry_root_parse import parse_registry_root_envelope
from llmix.types import InvalidConfigError, SecurityError

DOMAIN = "registry.example.com"


def test_top_level_star_import_exports_trust_manifest_api() -> None:
    namespace: dict[str, Any] = {}
    exec("from llmix import *", namespace)

    for name in [
        "LLMIX_TRUST_MANIFEST_KIND",
        "LLMIX_TRUST_MANIFEST_VERSION",
        "LlmixTrustManifest",
        "LlmixTrustManifestRegistryRoot",
        "LlmixTrustManifestReleasePlan",
        "load_llmix_trust_manifest",
        "parse_llmix_trust_manifest",
        "registry_root_options_from_trust_manifest",
    ]:
        assert name in namespace


class RootDidWebVerifier:
    def __init__(
        self, *, ok: bool = True, expected_payload_type: str = REGISTRY_ROOT_PAYLOAD_TYPE
    ) -> None:
        self.ok = ok
        self.expected_payload_type = expected_payload_type
        self.seen: DidWebVerificationInput | None = None

    def verify(self, input_value: DidWebVerificationInput) -> bool:
        self.seen = input_value
        return (
            self.ok
            and input_value.domain == DOMAIN
            and input_value.payload_type == self.expected_payload_type
        )


def _trust_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "trustedSigners": [{"type": "did-web", "domain": DOMAIN}],
    }


def _root_signer(input_value: RegistryRootSigningInput) -> dict[str, Any]:
    return {
        "signer": f"did-web:{DOMAIN}",
        "key-id": f"did:web:{DOMAIN}#root",
        "payload-digest": input_value.integrity["digest"],
        "algorithm": "ed25519",
        "signature": "fixture-registry-root-signature",
        "payload-type": input_value.payload_type,
    }


def _legacy_registry_root_payload_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _resign_registry_root_legacy_pretty(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    updated = deepcopy(envelope)
    payload_sha256 = hashlib.sha256(
        _legacy_registry_root_payload_bytes(updated["payload"])
    ).hexdigest()
    digest = f"sha256:{payload_sha256}"
    updated["payload_sha256"] = payload_sha256
    updated["integrity"] = {"algorithm": "sha256", "digest": digest}
    updated["signatures"] = [
        {
            "signer": f"did-web:{DOMAIN}",
            "key-id": f"did:web:{DOMAIN}#root",
            "payload-digest": digest,
            "algorithm": "ed25519",
            "signature": "fixture-registry-root-signature",
            "payload-type": REGISTRY_ROOT_PAYLOAD_TYPE,
        }
    ]
    return updated


def _publish_options() -> ConfigRegistryPublishOptions:
    return ConfigRegistryPublishOptions(
        registry_root=RegistryRootSigningOptions(signer=_root_signer)
    )


def _open_options(
    *,
    verifier: RootDidWebVerifier | None = None,
    expected_revision: str | None = None,
    expected_root_digest: str | None = None,
    minimum_published_at: str | None = None,
    high_watermark: Any | None = None,
) -> ConfigRegistryOpenOptions:
    return ConfigRegistryOpenOptions(
        signed_root=RegistryRootVerificationOptions(
            trust_policy=_trust_policy(),
            did_web_verifier=verifier or RootDidWebVerifier(),
            expected_revision=expected_revision,
            expected_root_digest=expected_root_digest,
            minimum_published_at=minimum_published_at,
            high_watermark=high_watermark,
        )
    )


def _write_authoring_preset(
    root: Path,
    *,
    model: str = "gpt-4.1-mini",
    temperature: float = 0.2,
) -> Path:
    path = root / "authoring" / "search" / "summary.mda"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "name": "summary",
        "description": "Search summary.",
        "metadata": {
            "snoai-llmix": {
                "common": {
                    "provider": "openai",
                    "model": model,
                    "temperature": temperature,
                }
            }
        },
    }
    path.write_text(
        f"---\n{json.dumps(frontmatter, indent=2)}\n---\n# summary\n",
        encoding="utf-8",
    )
    return path


def _write_signed_authoring_preset(root: Path) -> Path:
    path = root / "authoring" / "search" / "summary.mda"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# signed summary\n"
    unsigned = {
        "name": "summary",
        "description": "Search summary.",
        "requires": {"network": ["none"]},
        "metadata": {
            "snoai-llmix": {
                "common": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "temperature": 0.2,
                }
            }
        },
    }
    digest = "sha256:" + hash_canonical(
        canonicalize_artifact(unsigned, body), "sha256"
    )
    signed = dict(unsigned)
    signed["integrity"] = {"algorithm": "sha256", "digest": digest}
    signed["signatures"] = [
        {
            "signer": f"did-web:{DOMAIN}",
            "key-id": f"did:web:{DOMAIN}#authoring",
            "payload-digest": digest,
            "algorithm": "ed25519",
            "signature": "fixture-authoring-signature",
            "payload-type": DEFAULT_PAYLOAD_TYPE,
        }
    ]
    path.write_text(
        f"---\n{json.dumps(signed, indent=2)}\n---\n{body}",
        encoding="utf-8",
    )
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _publish_signed_registry(root: Path, revision: str = "rev-1") -> None:
    _write_authoring_preset(root)
    ConfigRegistryPublisher(root).publish(revision=revision, options=_publish_options())


def test_publish_writes_signed_registry_root_and_manager_opens_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, model="gpt-5-mini", temperature=0.7)
    verifier = RootDidWebVerifier()

    published = ConfigRegistryPublisher(root).publish(
        revision="rev-1", options=_publish_options()
    )
    current = _read_json(root / "current.json")
    manager = ConfigRegistryManager.open(root, _open_options(verifier=verifier))
    config = manager.get_preset("search", "summary")

    assert published.registry_root_path is not None
    assert published.registry_root_sha256 is not None
    envelope = _read_json(published.registry_root_path)
    payload = envelope["payload"]
    paths = {file["path"] for file in payload["files"]}
    assert current["revision"] == "rev-1"
    assert current["manifest_sha256"] == published.manifest_sha256
    assert payload["schema"] == "llmix.config-registry.root"
    assert payload["schema_version"] == 1
    assert isinstance(payload["published_at"], str)
    assert payload["current"]["manifest_sha256"] == published.manifest_sha256
    assert payload["manifest"]["path"] == "snapshots/rev-1/manifest.json"
    assert "snapshots/rev-1/authoring/search/summary.mda" in paths
    assert "snapshots/rev-1/resolved/search/summary.json" in paths
    assert config["model"] == "gpt-5-mini"
    assert verifier.seen is not None
    assert verifier.seen.payload_type == REGISTRY_ROOT_PAYLOAD_TYPE


def test_signed_registry_root_opens_legacy_pretty_canonical_payload(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root)

    published = ConfigRegistryPublisher(root).publish(
        revision="rev-1", options=_publish_options()
    )
    assert published.registry_root_path is not None
    envelope = _resign_registry_root_legacy_pretty(
        _read_json(published.registry_root_path)
    )
    _write_json(published.registry_root_path, envelope)

    manager = ConfigRegistryManager.open(root, _open_options())
    assert manager.get_preset("search", "summary")["model"] == "gpt-4.1-mini"


def test_publish_passes_trusted_runtime_options_to_mda_loader(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_signed_authoring_preset(root)
    verifier = RootDidWebVerifier(expected_payload_type=DEFAULT_PAYLOAD_TYPE)

    published = ConfigRegistryPublisher(root).publish(
        revision="rev-1",
        options=ConfigRegistryPublishOptions(
            verify_integrity=True,
            verify_signatures=True,
            trusted_runtime=True,
            enforce_requires=True,
            allowed_networks=["none"],
            trust_policy=_trust_policy(),
            did_web_verifier=verifier,
        ),
    )

    assert published.preset_ids == ("search/summary",)
    assert verifier.seen is not None
    assert verifier.seen.payload_type == DEFAULT_PAYLOAD_TYPE


def test_signed_registry_root_rejects_resolved_artifact_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _publish_signed_registry(root)
    resolved = root / "snapshots" / "rev-1" / "resolved" / "search" / "summary.json"
    data = _read_json(resolved)
    data["model"] = "tampered"
    _write_json(resolved, data)

    with pytest.raises(SecurityError, match="file digest mismatch"):
        ConfigRegistryManager.open(root, _open_options())


def test_signed_registry_root_rejects_current_manifest_digest_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _publish_signed_registry(root)
    current = _read_json(root / "current.json")
    current["manifest_sha256"] = "0" * 64
    _write_json(root / "current.json", current)

    with pytest.raises(InvalidConfigError, match="manifest"):
        ConfigRegistryManager.open(root, _open_options())


def test_registry_root_parser_rejects_current_binding_digest_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _publish_signed_registry(root)
    root_path = root / "snapshots" / "rev-1" / "registry-root.json"
    envelope = _read_json(root_path)
    envelope["payload"]["current"]["sha256"] = "0" * 64

    with pytest.raises(InvalidConfigError, match="current binding digest"):
        parse_registry_root_envelope(envelope, str(root_path))


def test_unsigned_registry_rejects_manifest_digest_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root)
    published = ConfigRegistryPublisher(root).publish(revision="rev-1")
    manifest = _read_json(published.manifest_path)
    manifest["published_at"] = "2999-01-01T00:00:00Z"
    _write_json(published.manifest_path, manifest)

    with pytest.raises(InvalidConfigError, match="Checksum mismatch"):
        ConfigRegistryManager.open(root)


def test_signed_registry_root_rejects_malformed_root_payload(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _publish_signed_registry(root)
    root_path = root / "snapshots" / "rev-1" / "registry-root.json"
    original = _read_json(root_path)
    envelope = deepcopy(original)
    del envelope["payload"]["schema"]
    _write_json(root_path, envelope)

    with pytest.raises(InvalidConfigError, match="schema"):
        ConfigRegistryManager.open(root, _open_options())

    envelope = deepcopy(original)
    del envelope["payload"]["published_at"]
    _write_json(root_path, envelope)

    with pytest.raises(InvalidConfigError, match="published_at"):
        ConfigRegistryManager.open(root, _open_options())


def test_signed_registry_root_enforces_expected_revision_and_freshness(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _publish_signed_registry(root)

    with pytest.raises(SecurityError, match="revision mismatch"):
        ConfigRegistryManager.open(root, _open_options(expected_revision="rev-2"))

    with pytest.raises(SecurityError, match="published_at"):
        ConfigRegistryManager.open(
            root, _open_options(minimum_published_at="2999-01-01T00:00:00Z")
        )

    with pytest.raises(SecurityError, match="high-watermark"):
        ConfigRegistryManager.open(root, _open_options(high_watermark=lambda _: False))


def test_signed_registry_root_expected_digest_pins_published_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root)
    published = ConfigRegistryPublisher(root).publish(
        revision="rev-1", options=_publish_options()
    )

    assert published.registry_root_sha256 is not None
    manager = ConfigRegistryManager.open(
        root, _open_options(expected_root_digest=published.registry_root_sha256)
    )
    assert manager.get_preset("search", "summary")["model"] == "gpt-4.1-mini"

    with pytest.raises(SecurityError, match="expected_root_digest"):
        ConfigRegistryManager.open(root, _open_options(expected_root_digest="0" * 64))


def test_signed_registry_root_opens_from_cli_trust_manifest_schema(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root)
    published = ConfigRegistryPublisher(root).publish(
        revision="rev-1", options=_publish_options()
    )

    assert published.registry_root_sha256 is not None
    assert published.registry_root_path is not None
    envelope = _read_json(published.registry_root_path)
    manifest_path = tmp_path / "llmix-trust.json"
    manifest = {
        "version": 1,
        "kind": "llmix-trust-manifest",
        "expectedRootDigest": f"sha256:{published.registry_root_sha256}",
        "sourceSetDigest": f"sha256:{'1' * 64}",
        "releasePlanDigest": f"sha256:{'2' * 64}",
        "registryRootTrustPolicy": _trust_policy(),
        "rekorPolicy": None,
        "minimumRevision": None,
        "minimumPublishedAt": None,
        "highWatermark": None,
        "registryRootSignerIdentity": {"type": "did-web", "domain": DOMAIN},
        "registryRoot": {
            "path": str(published.registry_root_path),
            "revision": published.revision,
            "publishedAt": envelope["payload"]["published_at"],
            "highWatermark": published.revision,
        },
        "releasePlan": {"path": "release-plan.json", "sourceCount": 1},
    }
    _write_json(manifest_path, manifest)

    options = registry_root_options_from_trust_manifest(
        load_llmix_trust_manifest(manifest_path),
        did_web_verifier=RootDidWebVerifier(),
    )
    manager = ConfigRegistryManager.open(
        root, ConfigRegistryOpenOptions(signed_root=options)
    )

    assert manager.get_preset("search", "summary")["model"] == "gpt-4.1-mini"

    manifest_with_watermark = deepcopy(manifest)
    manifest_with_watermark["highWatermark"] = published.revision
    _write_json(manifest_path, manifest_with_watermark)
    options_with_watermark = registry_root_options_from_trust_manifest(
        load_llmix_trust_manifest(manifest_path),
        did_web_verifier=RootDidWebVerifier(),
    )
    assert options_with_watermark.minimum_revision == published.revision
    manager_with_watermark = ConfigRegistryManager.open(
        root, ConfigRegistryOpenOptions(signed_root=options_with_watermark)
    )
    assert manager_with_watermark.get_preset("search", "summary")["model"] == "gpt-4.1-mini"

    manifest_without_signer = deepcopy(manifest)
    del manifest_without_signer["registryRootSignerIdentity"]
    _write_json(manifest_path, manifest_without_signer)
    with pytest.raises(InvalidConfigError, match="registryRootSignerIdentity"):
        load_llmix_trust_manifest(manifest_path)


def test_failed_registry_root_signing_does_not_activate_new_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _publish_signed_registry(root, revision="rev-1")
    before = _read_json(root / "current.json")
    _write_authoring_preset(root, model="gpt-5-mini")

    def fail_signer(_: RegistryRootSigningInput) -> dict[str, Any]:
        raise InvalidConfigError("signing backend unavailable")

    with pytest.raises(InvalidConfigError, match="signing backend unavailable"):
        ConfigRegistryPublisher(root).publish(
            revision="rev-2",
            options=ConfigRegistryPublishOptions(
                registry_root=RegistryRootSigningOptions(signer=fail_signer)
            ),
        )

    assert _read_json(root / "current.json") == before
    assert not (root / "snapshots" / "rev-2").exists()
