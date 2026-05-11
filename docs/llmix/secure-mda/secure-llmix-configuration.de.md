# Sichere LLMix-MDA-Konfiguration

Sprachen: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix kann Modell-Presets aus signierten MDA-Dateien laden und daraus eine signierte Registry veröffentlichen. So kann Modellverhalten aus dem Anwendungscode herausgelöst werden, ohne dass nachgelagerte Nutzer es still ändern können.

Die wichtigste Regel ist einfach:

Die Registry darf mit der App ausgeliefert werden, aber der Trust Anchor muss außerhalb der Registry liegen.

Wenn ein Angreifer `config/llm/` ersetzen kann, kann er jede Datei darin ersetzen. Die Runtime darf also nicht nur den Dateien in `config/llm/` vertrauen. Sie muss `expectedRootDigest`, Trust Policy, Signer-Identität und Freshness-/Rollback-Regeln von außen bekommen.

## Quick Start

Nutze die aktuelle MDA CLI 1.1.x oder neuer. Der Ablauf unten wurde mit `mda --version` = `1.1.2` geprüft.

1. Schreibe LLMix-Presets als Source-`.mda`-Dateien.
2. Validiere, ergänze Integrity-Daten und signiere sie in CI oder im Release-Prozess.
3. Der LLMix Publisher verifiziert diese `.mda`-Dateien mit trusted runtime und veröffentlicht eine signierte Registry.
4. Die MDA CLI finalisiert ein externes Deployment Trust Manifest.
5. Die Runtime öffnet die Registry mit diesem externen Manifest.

```bash
mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out authoring/search_summary/openai_fast.mda

mda validate authoring/search_summary/openai_fast.mda --target source --json
mda integrity compute authoring/search_summary/openai_fast.mda --target source --write --json
```

Signiere mit dem Signer aus deinem Release-Prozess. Das einfachste lokale Beispiel ist did:web:

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Erzeuge Source Policy und Registry-Root Policy, bereite den Release Plan vor, veröffentliche die LLMix Registry und finalisiere dann das externe Manifest:

```bash
mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/source-policy.json \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/root-policy.json \
  --json

mda release prepare \
  --target llmix-registry \
  --source authoring \
  --registry-dir config/llm \
  --policy release/source-policy.json \
  --did-document release/did.json \
  --out release/plan.json \
  --json

# Hier den LLMix Publisher mit trustedRuntime ausführen.
# Er liest authoring/, verifiziert jede signierte .mda, schreibt config/llm/
# und signiert config/llm/snapshots/<revision>/registry-root.json.

mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/snapshots/<revision>/registry-root.json \
  --release-plan release/plan.json \
  --policy release/root-policy.json \
  --derive-root-digest \
  --minimum-revision <revision> \
  --out deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

mda doctor release \
  --target llmix-registry \
  --source authoring \
  --registry-dir config/llm \
  --release-plan release/plan.json \
  --manifest deploy/llmix-trust.json \
  --did-document release/did.json \
  --json
```

`deploy/llmix-trust.json` ist der externe Anchor. Lege diese Datei nicht unter `config/llm/` ab.

## Was geschützt wird

| Situation | Ergebnis |
| --- | --- |
| Signierte `.mda`-Dateien sind korrekt, und Registry Root passt zum externen Trust Manifest. | LLMix lädt das Preset. |
| Preset, Manifest, `current.json` oder Registry Root wurde nach der Veröffentlichung geändert. | Die Runtime lehnt die Registry ab. |
| Das ganze Verzeichnis `config/llm/` wird durch eine andere, intern konsistente Registry ersetzt. | Die Runtime lehnt trotzdem ab, weil `expectedRootDigest`, Signer Policy und Freshness-Regeln von außen kommen. |

Für Rollback-Schutz nutze beim Finalisieren und Öffnen `minimumRevision`, `minimumPublishedAt` oder einen High-Watermark-Wert.

## Dateien

Empfohlene Struktur:

```text
authoring/
  search_summary/
    openai_fast.mda
    openrouter_balanced.mda

config/llm/
  current.json
  snapshots/
    <revision>/
      manifest.json
      registry-root.json
      search_summary/
        openai_fast.json
        openrouter_balanced.json

deploy/
  llmix-trust.json
```

`authoring/` enthält von Menschen bearbeitete Source-`.mda`-Dateien. `config/llm/` enthält die veröffentlichte LLMix Registry und darf mit der App ausgeliefert werden. `deploy/llmix-trust.json` muss über einen separaten Deployment-Kanal kommen, zum Beispiel App Config, Secret/Config Manager, Kubernetes Config, eine in die App eingebaute Konstante oder Release Attestation.

`registry-root.json` ist der Nachweis. Das externe Trust Manifest ist der Anchor.

## Presets schreiben

MDA-Mechanikfelder stehen oben. LLMix-Einstellungen liegen unter `metadata.snoai-llmix`.

```markdown
---
name: openai_fast
description: Fast OpenAI preset for search summaries.
requires:
  network: ["api.openai.com"]
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 1024
integrity:
  algorithm: sha256
  digest: "sha256:..."
signatures:
  - signer: "did-web:config.example.com"
    key-id: "did:web:config.example.com#release"
    payload-digest: "sha256:..."
    algorithm: ed25519
    signature: "..."
    payload-type: "application/vnd.snoai-llmix.preset+json"
---

# Optional notes for humans
```

Nutze registry-sichere Namen für Module und Presets. Kleinbuchstaben, Zahlen, `_` und `-` sind die sicherste Wahl. Provider API Keys, Tenant Secrets und umgebungsspezifische Credentials gehören nicht in `.mda`; speichere sie in der Runtime-Umgebung oder im Secret Manager.

Die vollständige Provider-Config-Form steht in der [LLMix usage reference](../llmix-usage-ref.md).

## Publisher Contract

Beim Veröffentlichen einer Produktions-Registry sollte der Publisher:

1. Source-`.mda`-Dateien mit `trustedRuntime: true` laden.
2. Source Trust Policy und Network Policy durchsetzen.
3. Immutable resolved JSON snapshots schreiben.
4. `current.json` für die aktive Revision schreiben.
5. `registry-root.json` für die gesamte Registry-Revision schreiben und signieren.

Der Registry Root deckt Active Pointer, Snapshot Manifest, resolved config files, Source Digests, Release Revision und Veröffentlichungszeit ab. Einzelne Änderungen werden dadurch erkannt. Ein kompletter Austausch wird durch externes `expectedRootDigest` und Trust Policy erkannt.

## Runtime

Die Runtime öffnet `config/llm/` mit `signedRoot`-Optionen, die aus dem externen Trust Manifest entstehen.

TypeScript:

```ts
import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const manifest = await loadLlmixTrustManifest("/etc/llmix/llmix-trust.json");

const registry = await ConfigRegistryManager.open("./config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(manifest, {
    didWebVerifier,
    rekorClient,
    sigstoreVerifier,
    highWatermark,
  }),
});

const preset = await registry.getPreset("search_summary", "openai_fast");
```

Python:

```python
from llmix import (
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    load_llmix_trust_manifest,
    registry_root_options_from_trust_manifest,
)

manifest = load_llmix_trust_manifest("/etc/llmix/llmix-trust.json")

registry = ConfigRegistryManager.open(
    "./config/llm",
    ConfigRegistryOpenOptions(
        signed_root=registry_root_options_from_trust_manifest(
            manifest,
            did_web_verifier=did_web_verifier,
            rekor_client=rekor_client,
            sigstore_verifier=sigstore_verifier,
            high_watermark=high_watermark,
        )
    ),
)

preset = registry.get_preset("search_summary", "openai_fast")
```

Rust:

```rust
use llmix_rs::{
    registry_root_options_from_trust_manifest,
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    load_llmix_trust_manifest,
};

let manifest = load_llmix_trust_manifest("/etc/llmix/llmix-trust.json")?;
let signed_root = registry_root_options_from_trust_manifest(&manifest)?;

let registry = ConfigRegistryManager::open_with_options(
    "./config/llm",
    ConfigRegistryOpenOptions {
        signed_root: Some(signed_root),
    },
)?;

let preset = registry.get_preset("search_summary", "openai_fast")?;
```

Wenn die Policy did:web vertraut, muss die App einen did:web Verifier bereitstellen. Wenn die Policy GitHub Actions/Sigstore vertraut, braucht sie Sigstore- und Rekor-Verifier.

## Anchor-Auswahl

Wähle den Anchor, der am besten zu deinem Deployment passt.

| Anchor | Passt für | Hinweise |
| --- | --- | --- |
| Externe Trust-Manifest-Datei | Die meisten Services | Von `mda release finalize` erzeugt und außerhalb von `config/llm/` gespeichert. Einfachster Default. |
| App-Konstante oder Build-Time Config | CLI, Desktop, Embedded App | `expectedRootDigest` und Policy werden in die App gepinnt. Für eine neue Registry muss App oder Build Config aktualisiert werden. |
| Deployment Config oder Secret Manager | Server Deployments | Liegt in Kubernetes Config, Cloud Config, Secret Manager, SSM, Vault oder ähnlichem. |
| GitHub Actions OIDC + Rekor | Üblicher CI-Release-Flow | Gut, wenn Releases aus einem Repo Workflow kommen. Die Policy pinnt Repo, Workflow, Ref, Issuer und Rekor. |
| did:web, KMS oder HSM | Organisationen mit eigener Signing-Infrastruktur | Gut, wenn bereits Web Identity oder Key Management vorhanden ist. |

Die MDA CLI kann Policies erzeugen, Sources validieren, Signaturen prüfen, Release Plans vorbereiten, Trust Manifests finalisieren und Deployment Snippets ausgeben. Sie ersetzt aber nicht die endgültige Trust Boundary der Runtime. Runtime Trust kommt weiterhin aus dem externen Anchor, den du an LLMix übergibst.

## Deployment-Snippets

Nach `deploy/llmix-trust.json` kann die CLI aus demselben Manifest Deployment-Snippets erzeugen:

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Unterstützt werden `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python` und `rust`.

## Troubleshooting

| Problem | Prüfen |
| --- | --- |
| Eine korrekte Registry öffnet nicht und meldet einen Digest-Fehler. | Prüfe, dass `expectedRootDigest` der SHA-256 der `registry-root.json`-Datei-Bytes ist, nicht nur des inneren Payloads. Führe `mda release finalize --derive-root-digest` erneut aus. |
| Runtime meldet, dass keine vertrauenswürdige Signatur existiert. | Die Signatur kann kryptografisch gültig sein, aber nicht zu `trustedSigners` passen. Prüfe Signer Type, Domain, Issuer, Subject, Workflow und Ref. |
| did:web verification schlägt fehl. | Stelle sicher, dass der Runtime did:web Verifier dasselbe DID Document auflöst, das im Release verwendet wurde, und dass `key-id` darin existiert. |
| Sigstore verification schlägt fehl. | Prüfe Rekor Policy, Issuer, Subject, Workflow/Ref Binding und ob die Runtime Rekor Client und Sigstore Verifier bereitstellt. |
| Eine geänderte Datei scheint trotzdem zu laden. | Stelle sicher, dass die App die Registry mit `signedRoot`-Optionen öffnet. Ohne Signed-Root-Verification wird nur die Registry geparst. |
| Eine komplett ersetzte Registry lädt. | Das Trust Manifest wird wahrscheinlich aus `config/llm/` oder aus demselben ersetzten Paket gelesen. Lege es außerhalb der Registry ab. |
| Eine alte signierte Registry wird wieder aktiv. | Nutze `minimumRevision`, `minimumPublishedAt` oder einen High-Watermark-Wert beim Finalisieren und beim Runtime Open. |

## Weitere Docs

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
