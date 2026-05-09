#!/usr/bin/env python3
"""Public MDA config loader coverage."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import pytest
from snoai_mda_config import (
    AllowedSigner,
    RekorEntry,
    SignatureEntry,
    SigstoreVerificationResult,
    TrustPolicy,
)
from snoai_mda_config.integrity import canonicalize_artifact, hash_canonical

import llmix
import llmix.mda_loader as mda_loader
from llmix import config as config_facade
from llmix.config import (
    MdaConfigLoadOptions,
    build_mda_config_file_path,
    load_mda_config,
    load_mda_config_from_file,
    load_mda_config_preset,
)
from llmix.types import ConfigAccessError, InvalidConfigError, SecurityError

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
PRESET_PATH = FIXTURE_DIR / "sample_preset.mda"


def _base_frontmatter(**overrides: Any) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {
        "name": "sample-preset",
        "description": "Sample preset",
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
    frontmatter.update(overrides)
    return frontmatter


def _write_mda(
    path: Path, frontmatter: dict[str, Any], body: str = "# sample\n"
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n{json.dumps(frontmatter, indent=2)}\n---\n{body}", encoding="utf-8"
    )
    return path


SIGNER = "sigstore-oidc:https://accounts.google.com#releases@snoai.com"
KEY_ID = "fulcio:test-key"
SIGNATURE = "MEUCIQDkXFIXTUREONLYBASE64=="


class FakeRekorClient:
    def __init__(self, entry: RekorEntry | None) -> None:
        self.entry = entry

    def fetch_entry(self, log_id: str, log_index: int) -> RekorEntry | None:
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


def _trust_policy() -> TrustPolicy:
    return TrustPolicy(
        allowed_signers=(
            AllowedSigner(
                "https://accounts.google.com", re.compile(r"^releases@snoai\.com$")
            ),
        )
    )


def _write_signed_mda(path: Path) -> tuple[Path, dict[str, str]]:
    body = "# sample\n"
    frontmatter = _base_frontmatter()
    digest = "sha256:" + hash_canonical(canonicalize_artifact(frontmatter, body), "sha256")
    integrity = {"algorithm": "sha256", "digest": digest}
    signed = dict(frontmatter)
    signed["integrity"] = integrity
    signed["signatures"] = [
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
    return _write_mda(path, signed, body), integrity


def _rekor_entry(integrity: dict[str, str]) -> RekorEntry:
    payload = json.dumps(integrity, separators=(",", ":"), sort_keys=True).encode()
    return {
        "kind": "dsse-v0.0.1",
        "certificate_pem": "",
        "dsse_envelope": {
            "payload_type": "application/vnd.mda.integrity+json",
            "payload": base64.b64encode(payload).decode(),
            "signatures": [{"sig": SIGNATURE, "keyid": KEY_ID}],
        },
    }


def test_load_mda_config_projects_fixture_to_python_runtime_shape() -> None:
    config = load_mda_config(PRESET_PATH)

    assert config["provider"] == "openai"
    assert config["model"] == "gpt-5-mini"
    assert config["common"]["temperature"] == 0.7
    assert config["common"]["max_output_tokens"] == 4096
    assert "provider" not in config["common"]
    assert "model" not in config["common"]
    assert config["provider_options"]["openai"]["reasoning_effort"] == "medium"
    assert config["caching"]["strategy"] == "memory"
    assert config["description"].startswith("Sample LLMix preset")
    assert config["tags"] == ["fixture"]


def test_load_mda_config_direct_module_import_matches_config_facade() -> None:
    assert mda_loader.load_mda_config(PRESET_PATH) == load_mda_config(PRESET_PATH)


def test_config_facade_reexports_mda_loader_api() -> None:
    assert config_facade.MdaConfigLoadOptions is mda_loader.MdaConfigLoadOptions
    assert (
        config_facade.build_mda_config_file_path
        is mda_loader.build_mda_config_file_path
    )
    assert config_facade.load_mda_config is mda_loader.load_mda_config
    assert (
        config_facade.load_mda_config_from_file is mda_loader.load_mda_config_from_file
    )
    assert config_facade.load_mda_config_preset is mda_loader.load_mda_config_preset


def test_package_lazy_export_uses_mda_loader_module() -> None:
    assert llmix.load_mda_config is mda_loader.load_mda_config
    assert llmix.load_mda_config(PRESET_PATH)["provider"] == "openai"


def test_load_mda_config_normalizes_nested_camel_case_keys(tmp_path: Path) -> None:
    config_path = _write_mda(
        tmp_path / "public-compat.mda",
        _base_frontmatter(
            description="Top-level description",
            tags=["top"],
            metadata={
                "snoai-llmix": {
                    "common": {
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "maxOutputTokens": 123,
                        "keepThinkingOutput": True,
                    },
                    "providerOptions": {"openai": {"reasoningEffort": "high"}},
                    "caching": {"strategy": "memory", "maxItems": 99},
                    "description": "Namespace description",
                    "tags": ["namespace"],
                },
                "other-vendor": {"kept": True},
            },
        ),
    )

    config = load_mda_config(config_path)

    assert config["common"]["max_output_tokens"] == 123
    assert config["common"]["keep_thinking_output"] is True
    assert config["provider_options"]["openai"]["reasoning_effort"] == "high"
    assert config["caching"]["max_items"] == 99
    assert config["description"] == "Namespace description"
    assert config["tags"] == ["namespace"]


def test_load_mda_config_preset_and_from_file_use_mda_suffix(tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "llm"
    module_dir = config_dir / "search"
    _write_mda(module_dir / "summary.mda", _base_frontmatter())

    assert (
        build_mda_config_file_path(config_dir, "search", "summary")
        == config_dir.resolve() / "search" / "summary.mda"
    )
    assert load_mda_config_preset("summary", module_dir)["model"] == "gpt-4.1-mini"
    assert load_mda_config_preset("summary.mda", module_dir)["provider"] == "openai"
    assert (
        load_mda_config_from_file(config_dir, "search", "summary")["model"]
        == "gpt-4.1-mini"
    )


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_yaml_paths_are_rejected_with_mda_error(tmp_path: Path, suffix: str) -> None:
    config_path = tmp_path / f"legacy{suffix}"
    config_path.write_text("provider: openai\nmodel: gpt-4.1-mini\n", encoding="utf-8")

    with pytest.raises(InvalidConfigError, match=r"\.mda"):
        load_mda_config(config_path)
    with pytest.raises(InvalidConfigError, match=r"\.mda"):
        load_mda_config_preset(f"legacy{suffix}", tmp_path)


def test_preset_name_path_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SecurityError):
        load_mda_config_preset("../escape", tmp_path)


def test_preset_symlink_escape_is_rejected(tmp_path: Path) -> None:
    module_dir = tmp_path / "config" / "llm" / "search"
    module_dir.mkdir(parents=True)
    outside_path = _write_mda(tmp_path / "outside.mda", _base_frontmatter())
    link_path = module_dir / "summary.mda"
    try:
        link_path.symlink_to(outside_path)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        load_mda_config_preset("summary", module_dir)


def test_load_mda_config_from_file_rejects_module_symlink_escape(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config" / "llm"
    outside_module_dir = tmp_path / "outside" / "search"
    outside_path = _write_mda(outside_module_dir / "summary.mda", _base_frontmatter())
    module_link = config_dir / "search"
    module_link.parent.mkdir(parents=True)
    try:
        module_link.symlink_to(outside_path.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        load_mda_config_from_file(config_dir, "search", "summary")


def test_explicit_mda_symlink_escape_is_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    outside_path = _write_mda(tmp_path / "outside.mda", _base_frontmatter())
    link_path = config_dir / "summary.mda"
    try:
        link_path.symlink_to(outside_path)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        load_mda_config(link_path)


def test_load_mda_config_rejects_missing_or_unknown_llmix_namespace(
    tmp_path: Path,
) -> None:
    missing_path = _write_mda(
        tmp_path / "missing.mda",
        {"name": "missing", "description": "Missing", "metadata": {}},
    )
    unknown_path = _write_mda(
        tmp_path / "unknown.mda",
        _base_frontmatter(
            metadata={
                "snoai-llmix": {
                    "common": {"provider": "openai", "model": "gpt-4.1-mini"},
                    "unexpected": True,
                }
            }
        ),
    )

    with pytest.raises(InvalidConfigError, match="MDA config failed"):
        load_mda_config(missing_path)
    with pytest.raises(InvalidConfigError, match="MDA config failed"):
        load_mda_config(unknown_path)


def test_full_mda_top_level_fields_are_allowed_but_mechanism_fields_are_not_projected(
    tmp_path: Path,
) -> None:
    config_path = _write_mda(
        tmp_path / "full.mda",
        _base_frontmatter(
            license="MIT",
            compatibility=">=1",
            **{
                "allowed-tools": "none",
                "doc-id": "doc-0001",
                "depends-on": [{"name": "base-doc", "version-range": "^1.0.0"}],
                "created-date": "2026-01-01T00:00:00Z",
                "updated-date": "2026-01-02T00:00:00Z",
            },
            title="Full",
            version="1.0.0",
            requires={"network": "none"},
            author="SNO",
            tags=["top"],
            relationships=[],
        ),
    )

    config = load_mda_config(config_path)

    assert config["provider"] == "openai"
    assert config["tags"] == ["top"]
    assert "requires" not in config
    assert "integrity" not in config
    assert "signatures" not in config


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda fm: fm["metadata"]["snoai-llmix"]["common"].update(
                {"temperature": 3}
            ),
            "MDA config failed",
        ),
        (
            lambda fm: fm["metadata"]["snoai-llmix"]["common"].update(
                {"maxRetries": -1}
            ),
            "MDA config failed",
        ),
        (
            lambda fm: fm["metadata"]["snoai-llmix"].update(
                {"caching": {"strategy": "bad"}}
            ),
            "MDA config failed",
        ),
        (
            lambda fm: (
                fm["metadata"]["snoai-llmix"]["common"].update(
                    {"provider": "anthropic"}
                ),
                fm["metadata"]["snoai-llmix"].update(
                    {
                        "providerOptions": {
                            "anthropic": {
                                "thinking": {"type": "enabled", "budgetTokens": 100}
                            }
                        }
                    }
                ),
            ),
            "budget_tokens",
        ),
    ],
)
def test_loader_rejects_invalid_runtime_values(
    tmp_path: Path, mutation: Any, match: str
) -> None:
    frontmatter = _base_frontmatter()
    mutation(frontmatter)
    config_path = _write_mda(tmp_path / "invalid.mda", frontmatter)

    with pytest.raises(InvalidConfigError, match=match):
        load_mda_config(config_path)


def test_integrity_verification_accepts_valid_digest_and_rejects_tampered_body(
    tmp_path: Path,
) -> None:
    frontmatter = _base_frontmatter()
    body = "# valid\n"
    digest = hash_canonical(canonicalize_artifact(frontmatter, body), "sha256")
    frontmatter["integrity"] = {"algorithm": "sha256", "digest": f"sha256:{digest}"}
    config_path = _write_mda(tmp_path / "integrity.mda", frontmatter, body)

    assert (
        load_mda_config(config_path, MdaConfigLoadOptions(verify_integrity=True))[
            "provider"
        ]
        == "openai"
    )

    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("# valid", "# tampered"),
        encoding="utf-8",
    )
    with pytest.raises(InvalidConfigError) as exc_info:
        load_mda_config(config_path, MdaConfigLoadOptions(verify_integrity=True))
    assert exc_info.value.__cause__ is not None


def test_load_mda_config_passes_mda_verification_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_mda(tmp_path / "signed.mda", _base_frontmatter())
    captured: dict[str, Any] = {}

    def fake_load_mda_source(path: Path, *, schema: Any, **kwargs: Any) -> Any:
        captured["path"] = path
        captured["schema"] = schema
        captured.update(kwargs)
        return schema.model_validate(_base_frontmatter())

    monkeypatch.setattr(mda_loader, "load_mda_source", fake_load_mda_source)
    trust_policy = {"trusted": ["example"]}
    rekor_client = object()
    sigstore_verifier = object()

    config = load_mda_config(
        config_path,
        MdaConfigLoadOptions(
            verify_integrity=True,
            verify_signatures=True,
            enforce_requires=True,
            allowed_networks=["none"],
            trust_policy=trust_policy,
            rekor_client=rekor_client,
            sigstore_verifier=sigstore_verifier,
        ),
    )

    assert config["provider"] == "openai"
    assert captured["path"] == config_path.resolve()
    assert captured["verify_integrity"] is True
    assert captured["verify_signatures"] is True
    assert captured["enforce_requires"] is True
    assert captured["allowed_networks"] == ["none"]
    assert captured["trust_policy"] is trust_policy
    assert captured["rekor_client"] is rekor_client
    assert captured["sigstore_verifier"] is sigstore_verifier


def test_load_mda_config_verify_signatures_happy_path(tmp_path: Path) -> None:
    config_path, integrity = _write_signed_mda(tmp_path / "signed.mda")

    config = load_mda_config(
        config_path,
        MdaConfigLoadOptions(
            verify_signatures=True,
            trust_policy=_trust_policy(),
            rekor_client=FakeRekorClient(_rekor_entry(integrity)),
            sigstore_verifier=FakeSigstoreVerifier(),
        ),
    )

    assert config["provider"] == "openai"
    assert config["model"] == "gpt-4.1-mini"


def test_load_mda_config_verify_signatures_fail_closed(tmp_path: Path) -> None:
    config_path, integrity = _write_signed_mda(tmp_path / "signed.mda")

    with pytest.raises(InvalidConfigError) as exc_info:
        load_mda_config(
            config_path,
            MdaConfigLoadOptions(
                verify_signatures=True,
                trust_policy=_trust_policy(),
                rekor_client=FakeRekorClient(_rekor_entry(integrity)),
                sigstore_verifier=FakeSigstoreVerifier(identity="other@snoai.com"),
            ),
        )

    assert exc_info.value.__cause__ is not None


def test_load_mda_config_permission_denied_raises_config_access_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_mda(tmp_path / "denied.mda", _base_frontmatter())
    original_read_bytes = Path.read_bytes

    def raise_permission_error(self: Path) -> bytes:
        if self == config_path:
            raise PermissionError()
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", raise_permission_error)

    with pytest.raises(ConfigAccessError):
        load_mda_config(config_path)
