#!/usr/bin/env python3
"""E2E trust-anchor coverage for signed MDA config registry loading."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import jcs
import pytest
from snoai_mda_config import DEFAULT_PAYLOAD_TYPE, DidWebVerificationInput, construct_dsse_pae
from snoai_mda_config.integrity import canonicalize_artifact, hash_canonical

from llmix import (
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    ConfigRegistryPublishOptions,
    ConfigRegistryPublisher,
    MdaConfigLoadOptions,
    RegistryRootSigningInput,
    RegistryRootSigningOptions,
    RegistryRootVerificationOptions,
    load_mda_config,
)
from llmix.config_registry_types import REGISTRY_ROOT_PAYLOAD_TYPE
from llmix.types import InvalidConfigError, SecurityError

TRUSTED_DOMAIN = "config-anchor.example.com"
ATTACKER_DOMAIN = "attacker.example.com"
SECRET_BY_DOMAIN = {
    TRUSTED_DOMAIN: b"trusted-domain-test-key",
    ATTACKER_DOMAIN: b"attacker-domain-test-key",
}


class LocalDidWebTrustAnchor:
    def __init__(self, trusted_domain: str = TRUSTED_DOMAIN) -> None:
        self.trusted_domain = trusted_domain
        self.seen: list[DidWebVerificationInput] = []

    def verify(self, input_value: DidWebVerificationInput) -> bool:
        self.seen.append(input_value)
        secret = SECRET_BY_DOMAIN.get(input_value.domain)
        if input_value.domain != self.trusted_domain or secret is None:
            return False
        expected = _signature(secret, input_value.key_id, input_value.pae_bytes)
        return hmac.compare_digest(input_value.signature, expected)


def _signature(secret: bytes, key_id: str, pae_bytes: bytes) -> str:
    digest = hmac.new(secret, key_id.encode() + b"\0" + pae_bytes, hashlib.sha256).hexdigest()
    return f"local-did-web:{digest}"


def _canonical_json(value: object) -> bytes:
    raw = jcs.canonicalize(value)
    return raw if isinstance(raw, bytes) else raw.encode("utf-8")


def _trust_policy(domain: str = TRUSTED_DOMAIN) -> dict[str, Any]:
    return {"version": 1, "trustedSigners": [{"type": "did-web", "domain": domain}]}


def _frontmatter(model: str = "gpt-5-mini", temperature: float = 0.17) -> dict[str, Any]:
    return {
        "name": "anchored-summary",
        "description": "Signed LLMix preset anchored by did:web trust policy.",
        "requires": {"network": ["none"]},
        "metadata": {
            "snoai-llmix": {
                "common": {
                    "provider": "openai",
                    "model": model,
                    "temperature": temperature,
                    "maxOutputTokens": 512,
                }
            }
        },
    }


def _write_signed_mda(root: Path, *, domain: str = TRUSTED_DOMAIN) -> Path:
    path = root / "source" / "research" / "summary.mda"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# Anchored summary preset\nUse concise factual summaries.\n"
    unsigned = _frontmatter()
    digest = "sha256:" + hash_canonical(canonicalize_artifact(unsigned, body), "sha256")
    integrity = {"algorithm": "sha256", "digest": digest}
    key_id = f"did:web:{domain}#release-key"
    signature = _signature(
        SECRET_BY_DOMAIN[domain],
        key_id,
        construct_dsse_pae(DEFAULT_PAYLOAD_TYPE, _canonical_json({"integrity": integrity})),
    )
    signed = dict(unsigned)
    signed["integrity"] = integrity
    signed["signatures"] = [
        {
            "signer": f"did-web:{domain}",
            "key-id": key_id,
            "payload-digest": digest,
            "algorithm": "ed25519",
            "signature": signature,
            "payload-type": DEFAULT_PAYLOAD_TYPE,
        }
    ]
    path.write_text(f"---\n{json.dumps(signed, indent=2)}\n---\n{body}", encoding="utf-8")
    return path


def _root_signer_for(domain: str):
    def sign(input_value: RegistryRootSigningInput) -> dict[str, Any]:
        key_id = f"did:web:{domain}#registry-root"
        signature = _signature(
            SECRET_BY_DOMAIN[domain],
            key_id,
            construct_dsse_pae(
                input_value.payload_type, input_value.canonical_payload.encode("utf-8")
            ),
        )
        return {
            "signer": f"did-web:{domain}",
            "key-id": key_id,
            "payload-digest": input_value.integrity["digest"],
            "algorithm": "ed25519",
            "signature": signature,
            "payload-type": input_value.payload_type,
        }

    return sign


def _publish_options(domain: str = TRUSTED_DOMAIN) -> ConfigRegistryPublishOptions:
    verifier = LocalDidWebTrustAnchor(domain)
    return ConfigRegistryPublishOptions(
        verify_integrity=True,
        verify_signatures=True,
        trusted_runtime=True,
        enforce_requires=True,
        allowed_networks=["none"],
        trust_policy=_trust_policy(domain),
        did_web_verifier=verifier,
        registry_root=RegistryRootSigningOptions(signer=_root_signer_for(domain)),
    )


def _open_options(expected_root_digest: str | None = None) -> ConfigRegistryOpenOptions:
    return ConfigRegistryOpenOptions(
        signed_root=RegistryRootVerificationOptions(
            trust_policy=_trust_policy(),
            did_web_verifier=LocalDidWebTrustAnchor(),
            expected_root_digest=expected_root_digest,
        )
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_e2e_signed_mda_and_registry_root_loads_expected_config(tmp_path: Path) -> None:
    root = tmp_path / "trusted-registry"
    source_path = _write_signed_mda(root)

    direct_config = load_mda_config(
        source_path,
        options=MdaConfigLoadOptions(
            trusted_runtime=True,
            verify_integrity=True,
            verify_signatures=True,
            enforce_requires=True,
            allowed_networks=["none"],
            trust_policy=_trust_policy(),
            did_web_verifier=LocalDidWebTrustAnchor(),
        ),
    )
    published = ConfigRegistryPublisher(root).publish(revision="2026-05-10.1", options=_publish_options())
    manager = ConfigRegistryManager.open(root, _open_options(published.registry_root_sha256))
    loaded = manager.get_preset("research", "summary")

    assert direct_config["model"] == "gpt-5-mini"
    assert loaded == direct_config
    print("LOADED_CONFIG_JSON=" + json.dumps(loaded, sort_keys=True))


def test_e2e_rejects_partially_tampered_signed_mda_and_revision(tmp_path: Path) -> None:
    root = tmp_path / "partial-tamper"
    source_path = _write_signed_mda(root)
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace("gpt-5-mini", "gpt-4o-mini"),
        encoding="utf-8",
    )

    with pytest.raises(InvalidConfigError, match="integrity-mismatch"):
        load_mda_config(
            source_path,
            options=MdaConfigLoadOptions(
                trusted_runtime=True,
                verify_integrity=True,
                verify_signatures=True,
                enforce_requires=True,
                allowed_networks=["none"],
                trust_policy=_trust_policy(),
                did_web_verifier=LocalDidWebTrustAnchor(),
            ),
        )

    clean_root = tmp_path / "partial-revision-tamper"
    _write_signed_mda(clean_root)
    published = ConfigRegistryPublisher(clean_root).publish(
        revision="2026-05-10.1", options=_publish_options()
    )
    resolved_path = clean_root / "compiled" / published.revision / "resolved" / "research" / "summary.json"
    resolved = _read_json(resolved_path)
    resolved["model"] = "gpt-4o-mini"
    _write_json(resolved_path, resolved)

    with pytest.raises(SecurityError, match="file digest mismatch"):
        ConfigRegistryManager.open(clean_root, _open_options(published.registry_root_sha256))


def test_e2e_rejects_whole_registry_swap_signed_by_untrusted_anchor(tmp_path: Path) -> None:
    trusted_root = tmp_path / "trusted-root"
    attacker_root = tmp_path / "attacker-root"
    _write_signed_mda(trusted_root)
    trusted = ConfigRegistryPublisher(trusted_root).publish(
        revision="2026-05-10.1", options=_publish_options()
    )
    _write_signed_mda(attacker_root, domain=ATTACKER_DOMAIN)
    attacker = ConfigRegistryPublisher(attacker_root).publish(
        revision="2026-05-10.2", options=_publish_options(ATTACKER_DOMAIN)
    )

    (trusted_root / "current.json").write_text(
        (attacker_root / "current.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    target_compiled = trusted_root / "compiled" / attacker.revision
    target_compiled.parent.mkdir(parents=True, exist_ok=True)
    for path in (attacker_root / "compiled" / attacker.revision).rglob("*"):
        if path.is_file():
            copied = target_compiled / path.relative_to(attacker.compiled_path)
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(path.read_bytes())

    with pytest.raises(SecurityError, match="expected_root_digest"):
        ConfigRegistryManager.open(trusted_root, _open_options(trusted.registry_root_sha256))
    with pytest.raises(SecurityError, match="signature verification failed"):
        ConfigRegistryManager.open(trusted_root, _open_options())
