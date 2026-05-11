# Configuration LLMix MDA sécurisée

Langues : [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix peut charger des presets de modèle depuis des fichiers MDA signés et les publier sous forme de registry signée. Cela permet de déplacer le comportement du modèle hors du code applicatif sans laisser les utilisateurs en aval le modifier silencieusement.

La règle essentielle est simple :

La registry peut être livrée avec l'application, mais le trust anchor doit rester en dehors de la registry.

Si un attaquant peut remplacer `config/llm/`, il peut remplacer chaque fichier à l'intérieur. Le runtime ne doit donc pas faire confiance uniquement à `config/llm/`. Il doit recevoir depuis l'extérieur `expectedRootDigest`, la trust policy, l'identité du signer et les règles de freshness/rollback.

## Quick Start

Utilisez MDA CLI 1.1.x ou une version plus récente. Le flux ci-dessous a été vérifié avec `mda --version` = `1.1.2`.

1. Écrivez les presets LLMix comme fichiers source `.mda`.
2. Validez, ajoutez l'integrity et signez ces fichiers dans CI ou dans l'automatisation de release.
3. Le publisher LLMix vérifie ces `.mda` avec trusted runtime puis publie une registry signée.
4. MDA CLI finalise un deployment trust manifest externe.
5. Au runtime, ouvrez la registry avec ce manifest externe.

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

Signez avec le signer utilisé par votre processus de release. L'exemple local le plus simple est did:web :

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Générez les policies source et registry-root, préparez le release plan, publiez la registry LLMix, puis finalisez le manifest externe :

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

# Exécutez ici le publisher LLMix avec trustedRuntime activé.
# Il lit authoring/, vérifie chaque .mda signé, écrit config/llm/,
# puis signe config/llm/snapshots/<revision>/registry-root.json.

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

`deploy/llmix-trust.json` est l'anchor externe. Ne le stockez pas dans `config/llm/`.

## Ce que cela protège

| Cas | Résultat attendu |
| --- | --- |
| Les `.mda` signés sont valides et le registry root correspond au trust manifest externe. | LLMix charge le preset. |
| Un preset, manifest, `current.json` ou registry root est modifié après publication. | Le runtime rejette la registry. |
| Quelqu'un remplace tout `config/llm/` par une autre registry cohérente en interne. | Le runtime la rejette quand même, car `expectedRootDigest`, signer policy et règles de freshness viennent de l'extérieur. |

Pour empêcher les rollbacks, utilisez `minimumRevision`, `minimumPublishedAt` ou une valeur high-watermark lors de la finalisation et de l'ouverture runtime.

## Fichiers

Structure recommandée :

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

`authoring/` contient les fichiers source `.mda` édités par des humains. `config/llm/` contient la registry LLMix publiée et peut être livré avec l'application. `deploy/llmix-trust.json` doit venir d'un autre canal de déploiement, comme la configuration applicative, un secret/config manager, une configuration Kubernetes, une constante intégrée à l'application ou une release attestation.

`registry-root.json` est la preuve. Le trust manifest externe est l'anchor.

## Écrire des presets

Les champs mécaniques MDA vont au niveau supérieur. Les réglages LLMix vont dans `metadata.snoai-llmix`.

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

Utilisez des noms sûrs pour la registry pour les modules et presets. Les lettres minuscules, chiffres, `_` et `-` sont le choix le plus sûr. Ne mettez pas les provider API keys, tenant secrets ou credentials propres à l'environnement dans `.mda`; stockez-les dans l'environnement runtime ou dans un secret manager.

La forme complète de la provider config est dans [LLMix usage reference](../llmix-usage-ref.md).

## Contrat du publisher

Lors de la publication d'une registry de production, le publisher doit :

1. Charger les fichiers source `.mda` avec `trustedRuntime: true`.
2. Appliquer la source trust policy et la network policy requise.
3. Écrire des snapshots JSON résolus et immuables.
4. Écrire `current.json` pour la révision active.
5. Écrire et signer `registry-root.json` pour toute la révision de registry.

Le registry root couvre l'active pointer, le snapshot manifest, les resolved config files, les source digests, la release revision et l'heure de publication. Une modification partielle est donc détectée. Un remplacement complet est détecté par l'`expectedRootDigest` externe et la trust policy.

## Runtime

Le runtime ouvre `config/llm/` avec des options `signedRoot` dérivées du trust manifest externe.

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

Si la policy fait confiance à did:web, fournissez un did:web verifier. Si elle fait confiance à GitHub Actions/Sigstore, fournissez les verifiers Sigstore et Rekor.

## Choisir un anchor

Choisissez l'anchor le plus simple qui correspond à votre déploiement.

| Anchor | Cas adapté | Notes |
| --- | --- | --- |
| Fichier trust manifest externe | La plupart des services | Généré par `mda release finalize` et stocké hors de `config/llm/`. C'est le défaut le plus simple. |
| Constante d'application ou build-time config | CLI, desktop, embedded app | Pin `expectedRootDigest` et policy dans l'application. Une nouvelle registry exige une mise à jour de l'app ou de la build config. |
| Deployment config ou secret manager | Déploiements serveur | Placez-le dans Kubernetes config, cloud config, Secret Manager, SSM, Vault ou équivalent. |
| GitHub Actions OIDC + Rekor | Flux CI release courant | Bon choix quand les releases viennent d'un workflow de repo. La policy pin repo, workflow, ref, issuer et Rekor. |
| did:web, KMS ou HSM | Signing contrôlé par l'organisation | Utile si l'organisation possède déjà une web identity ou un key management. |

MDA CLI peut générer les policies, valider les sources, vérifier les signatures, préparer les release plans, finaliser les trust manifests et émettre des snippets de déploiement. Elle ne remplace pas la trust boundary finale au runtime. La confiance runtime vient toujours de l'anchor externe passé à LLMix.

## Snippets de déploiement

Une fois `deploy/llmix-trust.json` créé, la CLI peut générer des snippets depuis le même manifest :

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Formats pris en charge : `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python` et `rust`.

## Troubleshooting

| Problème | Vérifier |
| --- | --- |
| Une registry correcte ne s'ouvre pas et signale une erreur de digest. | Confirmez que `expectedRootDigest` est le SHA-256 des bytes du fichier `registry-root.json`, pas seulement du payload interne. Relancez `mda release finalize --derive-root-digest`. |
| Runtime dit qu'aucune trusted signature n'existe. | La signature peut être valide cryptographiquement mais ne pas correspondre à `trustedSigners`. Vérifiez signer type, domain, issuer, subject, workflow et ref. |
| did:web verification échoue. | Assurez-vous que le did:web verifier runtime résout le même DID document que celui utilisé au release, et que `key-id` existe. |
| Sigstore verification échoue. | Vérifiez Rekor policy, issuer, subject, binding workflow/ref, et la présence d'un Rekor client et d'un Sigstore verifier dans la runtime. |
| Un fichier modifié semble encore charger. | Assurez-vous que l'app ouvre la registry avec les options `signedRoot`. Sans signed root verification, elle ne fait que parser la registry. |
| Une registry remplacée entièrement charge. | Le trust manifest est probablement lu depuis `config/llm/` ou depuis le paquet remplacé. Déplacez-le hors de la registry. |
| Une ancienne registry signée redevient active. | Utilisez `minimumRevision`, `minimumPublishedAt` ou un high-watermark lors de la finalisation et de l'ouverture runtime. |

## Docs liés

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
