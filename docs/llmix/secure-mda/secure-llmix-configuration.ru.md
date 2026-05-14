# Безопасная конфигурация LLMix с MDA

Языки: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

This localized page follows the single official LLMix Secure MDA flow. The full command reference is maintained in [English](./secure-llmix-configuration.md).

## Official Layout

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

Meanings are fixed:

- `source/`: human-edited MDA preset sources.
- `current.json`: machine-generated active registry pointer.
- `compiled/`: machine-generated signed and resolved registry output.
- The trust anchor must always be outside `config/llm`.

## Required Flow

1. Put source presets in `config/llm/source/<module>/<preset>.mda`.
2. Run MDA CLI validation, integrity, signing, verification, and release prepare.
3. Run the official LLMix publisher command/API.
4. Generate `config/llm/current.json` and `config/llm/compiled/`.
5. Run MDA CLI release finalize and doctor checks.
6. Deliver the trust anchor from outside `config/llm`.
7. Open `config/llm` at runtime through LLMix with the external trust anchor.

## Tools

Use only the MDA CLI and LLMix. Do not write an app-local compiler, registry generator, or custom directory structure.
This did:web example assumes `release/did-web-private-key.pem` and `release/did.json` already exist outside `config/llm`.

```bash
mkdir -p config/llm/source/search_summary release deploy
mda init --template llmix-preset --module search_summary --preset openai_fast --provider openai --model gpt-5-mini --out config/llm/source/search_summary/openai_fast.mda
mda validate config/llm/source/search_summary/openai_fast.mda --target source --json
mda integrity compute config/llm/source/search_summary/openai_fast.mda --target source --write --json
mda release trust policy --target llmix-registry --profile did-web --domain config.example.com --out release/trust-policy.json --json
mda sign config/llm/source/search_summary/openai_fast.mda --profile did-web --did did:web:config.example.com --key-id did:web:config.example.com#release --key-file release/did-web-private-key.pem --in-place --json
mda verify config/llm/source/search_summary/openai_fast.mda --target source --policy release/trust-policy.json --did-document release/did.json --json
mda release prepare --target llmix-registry --source config/llm/source --registry-dir config/llm --policy release/trust-policy.json --did-document release/did.json --out release/plan.json --json
llmix publish-registry --root config/llm --release-plan release/plan.json --revision 2026-05-14T000000Z --policy release/trust-policy.json --did-document release/did.json --root-did did:web:config.example.com --root-key-id did:web:config.example.com#release --root-key-file release/did-web-private-key.pem --json
mda release finalize --target llmix-registry --registry-dir config/llm --registry-root config/llm/compiled/2026-05-14T000000Z/registry-root.json --release-plan release/plan.json --policy release/trust-policy.json --derive-root-digest --minimum-revision 2026-05-14T000000Z --out deploy/llmix-trust.json --did-document release/did.json --json
mda doctor release --target llmix-registry --source config/llm/source --registry-dir config/llm --release-plan release/plan.json --manifest deploy/llmix-trust.json --did-document release/did.json --json
llmix check-registry --root config/llm --trust deploy/llmix-trust.json --preset search_summary/openai_fast --did-document release/did.json --tamper-proof --json
```

## Runtime Proof

At runtime, open `config/llm` through LLMix, pass signed-root/trust options from the external trust anchor, load one expected preset, and prove tamper rejection by modifying generated registry content while the external trust anchor still pins the trusted release.
