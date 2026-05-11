# Configuración segura de LLMix con MDA

Idiomas: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix puede cargar presets de modelo desde archivos MDA firmados y publicarlos como una registry firmada. Así puedes sacar el comportamiento del modelo del código de la aplicación sin permitir que usuarios posteriores lo cambien en silencio.

La regla importante es simple:

La registry puede viajar con la app, pero el trust anchor debe vivir fuera de la registry.

Si un atacante puede reemplazar `config/llm/`, puede reemplazar todos los archivos dentro. Por eso el runtime no debe confiar solo en `config/llm/`. Debe recibir desde fuera `expectedRootDigest`, la trust policy, la identidad del signer y las reglas de freshness/rollback.

## Quick Start

Usa MDA CLI 1.1.x o una versión más nueva. El flujo de abajo fue comprobado con `mda --version` = `1.1.2`.

1. Escribe los presets de LLMix como archivos source `.mda`.
2. Valida, agrega integrity y firma esos archivos en CI o en el proceso de release.
3. El publisher de LLMix verifica esos `.mda` con trusted runtime y publica una registry firmada.
4. MDA CLI finaliza un deployment trust manifest externo.
5. En runtime, abre la registry con ese manifest externo.

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

Firma con el signer que usa tu release. El ejemplo local más simple es did:web:

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Genera las policies de source y registry root, prepara el release plan, publica la registry de LLMix y luego finaliza el manifest externo:

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

# Ejecuta aquí el publisher de LLMix con trustedRuntime habilitado.
# Lee authoring/, verifica cada .mda firmado, escribe config/llm/ y
# firma config/llm/snapshots/<revision>/registry-root.json.

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

`deploy/llmix-trust.json` es el anchor externo. No lo guardes dentro de `config/llm/`.

## Qué protege

| Caso | Resultado esperado |
| --- | --- |
| Los `.mda` firmados son válidos y el registry root coincide con el trust manifest externo. | LLMix carga el preset. |
| Se cambia un preset, manifest, `current.json` o registry root después de publicar. | El runtime rechaza la registry. |
| Alguien reemplaza todo `config/llm/` con otra registry internamente consistente. | El runtime también la rechaza porque `expectedRootDigest`, signer policy y reglas de freshness vienen de fuera. |

Para proteger contra rollback, usa `minimumRevision`, `minimumPublishedAt` o un valor high-watermark al finalizar y al abrir.

## Archivos

Estructura recomendada:

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

`authoring/` contiene archivos source `.mda` editados por humanos. `config/llm/` contiene la registry de LLMix publicada y puede viajar con la app. `deploy/llmix-trust.json` debe venir por otro canal de despliegue, como app config, secret/config manager, Kubernetes config, una constante compilada en la app o una release attestation.

`registry-root.json` es evidencia. El trust manifest externo es el anchor.

## Escribir presets

Los campos mecánicos de MDA van en el nivel superior. La configuración propia de LLMix va en `metadata.snoai-llmix`.

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

Usa nombres seguros para registry en módulos y presets. Letras minúsculas, números, `_` y `-` son la opción más segura. No pongas provider API keys, tenant secrets ni credenciales de entorno en `.mda`; guárdalas en el runtime environment o en un secret manager.

La forma completa de provider config está en [LLMix usage reference](../llmix-usage-ref.md).

## Contrato del publisher

Al publicar una registry de producción, el publisher debe:

1. Cargar los source `.mda` con `trustedRuntime: true`.
2. Aplicar la source trust policy y la network policy requerida.
3. Escribir snapshots JSON resueltos e inmutables.
4. Escribir `current.json` para la revisión activa.
5. Escribir y firmar `registry-root.json` para toda la revisión de la registry.

El registry root cubre el active pointer, snapshot manifest, resolved config files, source digests, release revision y hora de publicación. Así se detectan ediciones parciales. Un reemplazo completo se detecta con el `expectedRootDigest` externo y la trust policy.

## Runtime

El runtime abre `config/llm/` con opciones `signedRoot` derivadas del trust manifest externo.

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

Si la policy confía en did:web, proporciona un did:web verifier. Si confía en GitHub Actions/Sigstore, proporciona verificadores Sigstore y Rekor.

## Elegir anchor

Elige el anchor más simple que encaje con tu despliegue.

| Anchor | Mejor caso | Notas |
| --- | --- | --- |
| Archivo trust manifest externo | La mayoría de servicios | Generado por `mda release finalize` y guardado fuera de `config/llm/`. Es el default más simple. |
| Constante de app o build-time config | CLI, desktop, embedded app | Fija `expectedRootDigest` y policy en la app. Para aceptar una nueva registry hay que actualizar la app o build config. |
| Deployment config o secret manager | Despliegues de servidor | Colócalo en Kubernetes config, cloud config, Secret Manager, SSM, Vault o similar. |
| GitHub Actions OIDC + Rekor | Release común desde CI | Bueno cuando los releases salen de un workflow del repo. La policy fija repo, workflow, ref, issuer y Rekor. |
| did:web, KMS o HSM | Signing controlado por la organización | Útil cuando el equipo ya tiene web identity o key management. |

MDA CLI puede generar policies, validar sources, verificar firmas, preparar release plans, finalizar trust manifests y emitir snippets de despliegue. No reemplaza el límite final de confianza del runtime. La confianza en runtime sigue viniendo del anchor externo que pasas a LLMix.

## Snippets de despliegue

Cuando exista `deploy/llmix-trust.json`, la CLI puede generar snippets desde el mismo manifest:

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Formatos soportados: `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python` y `rust`.

## Troubleshooting

| Problema | Qué revisar |
| --- | --- |
| Una registry correcta no abre y muestra error de digest. | Confirma que `expectedRootDigest` sea el SHA-256 de los bytes del archivo `registry-root.json`, no solo del payload interno. Vuelve a correr `mda release finalize --derive-root-digest`. |
| Runtime dice que no hay trusted signature. | La firma puede verificar criptográficamente pero no coincidir con `trustedSigners`. Revisa signer type, domain, issuer, subject, workflow y ref. |
| did:web verification falla. | Verifica que el did:web verifier del runtime resuelva el mismo DID document usado en release y que `key-id` exista. |
| Sigstore verification falla. | Revisa Rekor policy, issuer, subject, binding de workflow/ref y si el runtime tiene Rekor client y Sigstore verifier. |
| Un archivo modificado parece cargar. | Asegúrate de que la app abra la registry con opciones `signedRoot`. Sin signed root verification solo estás parseando la registry. |
| Una registry reemplazada completa carga. | Probablemente el trust manifest se lee desde `config/llm/` o desde el paquete reemplazado. Muévelo fuera de la registry. |
| Una registry firmada antigua vuelve a activarse. | Usa `minimumRevision`, `minimumPublishedAt` o un high-watermark al finalizar y al abrir en runtime. |

## Documentos relacionados

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
