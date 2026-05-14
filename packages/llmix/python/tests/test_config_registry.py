#!/usr/bin/env python3
"""Coverage for the Python Config Registry publish/load/reload path."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from snoai_mda_config import (
    RekorEntry,
    SignatureEntry,
    SigstoreVerificationResult,
)
from snoai_mda_config.integrity import canonicalize_artifact, hash_canonical

from llmix import (
    ConfigRegistryManager,
    ConfigRegistryPublishOptions,
    ConfigRegistryPublisher,
)
from llmix.types import ConfigNotFoundError, InvalidConfigError, SecurityError


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_authoring_preset(
    root: Path,
    module: str,
    preset: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    temperature: float = 0.2,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> Path:
    path = root / "source" / module / f"{preset}.mda"
    path.parent.mkdir(parents=True, exist_ok=True)
    common: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "temperature": temperature,
    }
    if max_output_tokens is not None:
        common["maxOutputTokens"] = max_output_tokens
    namespace: dict[str, Any] = {"common": common}
    if reasoning_effort is not None:
        namespace["providerOptions"] = {
            "openai": {"reasoningEffort": reasoning_effort}
        }
    frontmatter = {
        "name": preset,
        "description": f"{module}/{preset}",
        "metadata": {"snoai-llmix": namespace},
    }
    path.write_text(
        f"---\n{json.dumps(frontmatter, indent=2)}\n---\n# {preset}\n", encoding="utf-8"
    )
    return path


SIGNER = "sigstore-oidc:https://accounts.google.com"
KEY_ID = "fulcio:test-key"
SIGNATURE = "MEUCIQDkXFIXTUREONLYBASE64=="
REKOR_URL = "https://rekor.sigstore.dev"


class FakeRekorClient:
    rekor_url = REKOR_URL

    def __init__(self, entry: RekorEntry | None) -> None:
        self.entry = entry

    def fetch_entry(
        self, rekor_url: str, log_id: str, log_index: int
    ) -> RekorEntry | None:
        assert rekor_url == self.rekor_url
        assert log_id == "test-log"
        assert log_index == 42
        return self.entry


class FakeSigstoreVerifier:
    def __init__(self, identity: str = "releases@snoai.com") -> None:
        self.identity = identity

    def verify(
        self, entry: RekorEntry, signature: SignatureEntry, pae_bytes: bytes
    ) -> SigstoreVerificationResult:
        assert entry["kind"] == "dsse-v0.0.1"
        assert signature["signature"] == SIGNATURE
        assert pae_bytes.startswith(b"DSSEv1 ")
        return SigstoreVerificationResult(
            issuer="https://accounts.google.com",
            subject_alternative_name=self.identity,
        )


def _trust_policy(subject: str = "releases@snoai.com") -> dict[str, Any]:
    return {
        "version": 1,
        "trustedSigners": [
            {
                "type": "sigstore-oidc",
                "issuer": "https://accounts.google.com",
                "subject": subject,
            }
        ],
        "rekor": {"url": REKOR_URL},
    }


def _write_signed_authoring_preset(root: Path) -> dict[str, str]:
    path = root / "source" / "search" / "summary.mda"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# summary\n"
    frontmatter: dict[str, Any] = {
        "name": "summary",
        "description": "search/summary",
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
    digest = "sha256:" + hash_canonical(canonicalize_artifact(frontmatter, body), "sha256")
    integrity = {"algorithm": "sha256", "digest": digest}
    frontmatter["integrity"] = integrity
    frontmatter["signatures"] = [
        {
            "signer": SIGNER,
            "key-id": KEY_ID,
            "payload-digest": digest,
            "algorithm": "ecdsa-p256",
            "signature": SIGNATURE,
            "rekor-log-id": "test-log",
            "rekor-log-index": 42,
        }
    ]
    path.write_text(
        f"---\n{json.dumps(frontmatter, indent=2)}\n---\n{body}", encoding="utf-8"
    )
    return integrity


def _rekor_entry(integrity: dict[str, str]) -> RekorEntry:
    payload = json.dumps(
        {"integrity": integrity}, separators=(",", ":"), sort_keys=True
    ).encode()
    return {
        "kind": "dsse-v0.0.1",
        "log_id": "test-log",
        "log_index": 42,
        "inclusion_verified": True,
        "certificate_pem": "",
        "dsse_envelope": {
            "payload_type": "application/vnd.mda.integrity+json",
            "payload": base64.b64encode(payload).decode(),
            "signatures": [{"sig": SIGNATURE, "keyid": KEY_ID}],
        },
    }


def test_publish_creates_active_revision_and_manager_reads_resolved_json(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root,
        "search",
        "summary",
        model="gpt-5-mini",
        temperature=0.7,
        max_output_tokens=1024,
        reasoning_effort="high",
    )

    published = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    config = manager.get_preset("search", "summary")

    assert published.activated is True
    assert manager.active_revision == published.revision
    assert config["provider"] == "openai"
    assert config["model"] == "gpt-5-mini"
    assert config["common"]["temperature"] == 0.7
    assert config["common"]["max_output_tokens"] == 1024
    assert config["provider_options"]["openai"]["reasoning_effort"] == "high"
    assert "search/summary" in manager.available_presets()
    assert manager.last_successful_reload_at is not None
    assert manager.last_reload_failure_at is None
    snapshot_dir = root / "compiled" / published.revision
    assert (snapshot_dir / "source" / "search" / "summary.mda").exists()
    resolved = json.loads(
        (snapshot_dir / "resolved" / "search" / "summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert resolved["common"]["maxOutputTokens"] == 1024
    assert resolved["providerOptions"]["openai"]["reasoningEffort"] == "high"


def test_manager_reloads_after_current_revision_changes(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    first = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(
        root, "search", "summary", model="gpt-5-mini", temperature=0.9
    )
    second = ConfigRegistryPublisher(root).publish()

    config = manager.get_preset("search", "summary")

    assert first.revision != second.revision
    assert manager.active_revision == second.revision
    assert config["model"] == "gpt-5-mini"
    assert config["common"]["temperature"] == 0.9


def test_available_presets_refreshes_after_current_revision_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")

    ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(root, "rerank", "default")
    ConfigRegistryPublisher(root).publish()

    assert manager.available_presets() == ("rerank/default", "search/summary")


def test_manager_rolls_back_when_current_revision_points_to_older_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    first = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(
        root, "search", "summary", model="gpt-5-mini", temperature=0.9
    )
    second = ConfigRegistryPublisher(root).publish()
    assert second.revision != first.revision
    assert manager.get_preset("search", "summary")["model"] == "gpt-5-mini"

    (root / "current.json").write_text(
        json.dumps(
            {
                "revision": first.revision,
                "manifest_sha256": first.manifest_sha256,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == first.revision
    assert config["model"] == "gpt-4.1-mini"
    assert config["common"]["temperature"] == 0.2


def test_manager_ignores_authoring_edits_until_a_new_revision_is_published(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    published = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(
        root, "search", "summary", model="gpt-5-mini", temperature=0.9
    )
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == published.revision
    assert config["model"] == "gpt-4.1-mini"
    assert config["common"]["temperature"] == 0.2


def test_manager_reads_resolved_json_without_mda_loader_hot_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")
    ConfigRegistryPublisher(root).publish()

    def fail_loader(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("manager must not parse MDA at runtime")

    monkeypatch.setattr("llmix.config_registry.load_mda_config", fail_loader)

    manager = ConfigRegistryManager.open(root)
    assert manager.get_preset("search", "summary")["provider"] == "openai"


def test_manager_fails_fast_without_active_revision(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    root.mkdir(parents=True)

    with pytest.raises(ConfigNotFoundError):
        ConfigRegistryManager.open(root)


def test_manager_fails_fast_with_malformed_current_pointer(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    root.mkdir(parents=True)
    (root / "current.json").write_text('{"revision":42}\n', encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        ConfigRegistryManager.open(root)


def test_manager_opens_legacy_current_pointer_without_manifest_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini")
    published = ConfigRegistryPublisher(root).publish()
    (root / "current.json").write_text(
        json.dumps({"revision": published.revision}) + "\n", encoding="utf-8"
    )

    manager = ConfigRegistryManager.open(root)
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == published.revision
    assert config["model"] == "gpt-4.1-mini"
    assert manager.last_reload_error is None


def test_manager_keeps_last_known_good_config_when_pointer_changes_to_missing_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    published = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    (root / "current.json").write_text(
        json.dumps(
            {"revision": "missing-revision", "manifest_sha256": published.manifest_sha256}
        )
        + "\n",
        encoding="utf-8",
    )
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == published.revision
    assert config["model"] == "gpt-4.1-mini"
    assert isinstance(manager.last_reload_error, ConfigNotFoundError)
    assert manager.last_successful_reload_at is not None
    assert manager.last_reload_failure_at is not None


def test_publish_failure_leaves_active_revision_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    first = ConfigRegistryPublisher(root).publish()
    broken_path = root / "source" / "search" / "summary.mda"
    broken_path.write_text(
        "---\nname: broken\nmetadata: [broken\n---\n", encoding="utf-8"
    )

    with pytest.raises(InvalidConfigError):
        ConfigRegistryPublisher(root).publish()

    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert pointer["revision"] == first.revision
    assert not any(
        path.name.endswith(".tmp")
        for path in (root / "compiled" / ".staging").glob("*")
    )


def test_legacy_yaml_authoring_blocks_publish_without_changing_active_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")
    first = ConfigRegistryPublisher(root).publish()

    legacy_path = root / "source" / "search" / "legacy.yaml"
    legacy_path.write_text("provider: openai\nmodel: gpt-4.1-mini\n", encoding="utf-8")

    with pytest.raises(InvalidConfigError, match=r"\.mda"):
        ConfigRegistryPublisher(root).publish()

    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert pointer["revision"] == first.revision


def test_publish_rejects_authoring_module_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    outside_root = tmp_path / "outside"
    _write_authoring_preset(outside_root, "search", "summary")

    authoring_dir = root / "source"
    authoring_dir.mkdir(parents=True)
    try:
        (authoring_dir / "search").symlink_to(
            outside_root / "source" / "search", target_is_directory=True
        )
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        ConfigRegistryPublisher(root).publish()


def test_publish_rejects_authoring_file_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    outside_path = _write_authoring_preset(tmp_path / "outside", "search", "summary")
    module_dir = root / "source" / "search"
    module_dir.mkdir(parents=True)

    try:
        (module_dir / "summary.mda").symlink_to(outside_path)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        ConfigRegistryPublisher(root).publish()


def test_publish_can_enforce_mda_integrity(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    path = _write_authoring_preset(root, "search", "summary")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            '"metadata"',
            '"integrity": {"algorithm": "sha256", "digest": "sha256:bad"},\n  "metadata"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(InvalidConfigError):
        ConfigRegistryPublisher(root).publish(
            options=ConfigRegistryPublishOptions(verify_integrity=True)
        )


def test_publish_passes_mda_verification_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")
    captured: list[Any] = []

    def fake_load_mda_config(_path: Path, options: Any = None) -> dict[str, Any]:
        captured.append(options)
        return {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "common": {"temperature": 0.2},
        }

    monkeypatch.setattr("llmix.config_registry.load_mda_config", fake_load_mda_config)
    trust_policy = {"trusted": ["example"]}
    rekor_client = object()
    sigstore_verifier = object()

    ConfigRegistryPublisher(root).publish(
        options=ConfigRegistryPublishOptions(
            verify_integrity=True,
            verify_signatures=True,
            enforce_requires=True,
            allowed_networks=["none"],
            trust_policy=trust_policy,
            rekor_client=rekor_client,
            sigstore_verifier=sigstore_verifier,
        )
    )

    assert len(captured) == 1
    options = captured[0]
    assert options.verify_integrity is True
    assert options.verify_signatures is True
    assert options.enforce_requires is True
    assert options.allowed_networks == ["none"]
    assert options.trust_policy is trust_policy
    assert options.rekor_client is rekor_client
    assert options.sigstore_verifier is sigstore_verifier


def test_registry_publish_verify_signatures_happy_path(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    integrity = _write_signed_authoring_preset(root)

    published = ConfigRegistryPublisher(root).publish(
        options=ConfigRegistryPublishOptions(
            verify_signatures=True,
            trust_policy=_trust_policy(),
            rekor_client=FakeRekorClient(_rekor_entry(integrity)),
            sigstore_verifier=FakeSigstoreVerifier(),
        )
    )
    manager = ConfigRegistryManager.open(root)

    assert published.activated is True
    assert manager.get_preset("search", "summary")["model"] == "gpt-4.1-mini"


def test_registry_publish_verify_signatures_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    integrity = _write_signed_authoring_preset(root)

    with pytest.raises(InvalidConfigError):
        ConfigRegistryPublisher(root).publish(
            options=ConfigRegistryPublishOptions(
                verify_signatures=True,
                trust_policy=_trust_policy(),
                rekor_client=FakeRekorClient(_rekor_entry(integrity)),
                sigstore_verifier=FakeSigstoreVerifier(identity="other@snoai.com"),
            )
        )


def test_manager_rejects_tampered_resolved_snapshot_on_startup(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    published = ConfigRegistryPublisher(root).publish()
    resolved_path = (
        root / "compiled" / published.revision / "resolved" / "search" / "summary.json"
    )
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["model"] = "tampered-model"
    resolved_path.write_text(json.dumps(resolved), encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        ConfigRegistryManager.open(root)


def test_manager_revalidates_resolved_json_shape_on_startup(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(
        root, "search", "summary", model="gpt-4.1-mini", temperature=0.2
    )

    published = ConfigRegistryPublisher(root).publish()
    snapshot_dir = root / "compiled" / published.revision
    manifest_path = snapshot_dir / "manifest.json"
    resolved_path = snapshot_dir / "resolved" / "search" / "summary.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["common"]["temperature"] = 3
    resolved_bytes = _canonical_json_bytes(resolved)
    resolved_path.write_bytes(resolved_bytes)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presets"]["search/summary"]["resolved_sha256"] = hashlib.sha256(
        resolved_bytes
    ).hexdigest()
    manifest_bytes = _canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    current_path = root / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    current_path.write_bytes(_canonical_json_bytes(current))

    with pytest.raises(InvalidConfigError, match="temperature"):
        ConfigRegistryManager.open(root)
