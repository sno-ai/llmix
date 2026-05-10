# Trusted Runtime Policy

Parsing a file is not trust. Verifying a digest is not trust either. It only
says the bytes match the digest in the file.

Use `trustedRuntime: true` when the config must come from a trusted signer.

```ts
import { loadMdaSource } from "@snoai/mda-config";

await loadMdaSource(path, schema, {
  trustedRuntime: true,
  trustPolicy: {
    version: 1,
    trustedSigners: [
      {
        type: "sigstore-oidc",
        issuer: "https://token.actions.githubusercontent.com",
        subject: "repo:OWNER/REPO:ref:refs/heads/main",
      },
    ],
    rekor: {
      url: "https://rekor.sigstore.dev",
    },
  },
  rekorClient,
  sigstoreVerifier,
});
```

## Policy Shape

The policy is intentionally small:

- `version` must be `1`.
- `trustedSigners` must be a non-empty array.
- Sigstore signers use `{ type: "sigstore-oidc", issuer, subject }`.
- did:web signers use `{ type: "did-web", domain }`.
- `minSignatures` is optional and defaults to `1`.
- Unknown fields are rejected with `trust-policy-violation`.

When any Sigstore signer is trusted, `rekor.url` is required. A did-web-only
policy must not contain `rekor`. A mixed policy is valid, and the Rekor URL
applies only to Sigstore signatures.

## What Trusted Runtime Enforces

With `trustedRuntime: true`, the loader:

1. validates `trustPolicy`;
2. requires `integrity`;
3. requires a non-empty `signatures[]`;
4. verifies the integrity digest;
5. checks every signature payload digest against `integrity.digest`;
6. verifies each candidate signature cryptographically;
7. matches verified identities against `trustedSigners`;
8. counts distinct trusted identities against `minSignatures`.

Distinct Sigstore identity is:

```text
("sigstore-oidc", verifiedIssuer, verifiedSubject)
```

Distinct did:web identity is:

```text
("did-web", domain)
```

Multiple signatures from the same distinct identity count once.

`verifySignatures()` is a lower-level helper. It is useful for focused checks,
but it is not a full production load unless the caller also enforces the
trusted-runtime requirements above.

## Rekor and Sigstore Hooks

This package does not ship a Rekor HTTP client. Your application supplies one:

```ts
const rekorClient = {
  async fetchEntry(rekorUrl, logId, logIndex) {
    const resp = await fetch(`${rekorUrl}/api/v1/log/entries/${logIndex}`);
    if (!resp.ok) return null;
    return await resp.json();
  },
};
```

For Sigstore candidates, the loader requires:

- the Rekor client URL to match `trustPolicy.rekor.url`;
- the fetched entry to match the signature's `rekor-log-id` and
  `rekor-log-index`;
- the Rekor entry kind to be `dsse-v0.0.1`;
- inclusion verification to have succeeded;
- the DSSE payload type, payload bytes, signature bytes, and key id to bind to
  the current signature and artifact;
- the Sigstore verifier hook to validate the Fulcio chain, current PAE bytes,
  and signature evidence;
- verified issuer to equal the issuer in `signatures[i].signer`;
- verified subject to exactly match a Sigstore `trustedSigners` subject.

That is the boundary. The loader handles the MDA rules. Your hooks handle
transport and cryptography.

## did:web

A did:web signature uses:

```yaml
signer: "did-web:tools.example.com"
```

It must not contain `rekor-log-id` or `rekor-log-index`.

Domain matching alone is never trust. If a policy trusts did:web and no
`didWebVerifier` hook is supplied, loading fails closed with
`trust-policy-violation`.

If one did:web candidate fails, another distinct trusted identity can still
satisfy `minSignatures`.

## Boundaries

This package does not:

- sign new MDA artifacts;
- ship built-in Rekor transport;
- refresh remote configs;
- keep a previous-good config after refresh failure.

If your application refreshes config at runtime, keep your own previous-good
verified config. A loader should say yes or no. It should not invent policy
after the room is already on fire.
