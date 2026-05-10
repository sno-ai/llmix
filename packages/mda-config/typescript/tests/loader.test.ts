import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { loadMdaSource, loadMdaSourceFromBytes } from "../src/loader.js";
import { ErrorCategory, MdaConfigError } from "../src/errors.js";
import {
  DEFAULT_PAYLOAD_TYPE,
  type DidWebVerifier,
  type RekorClient,
  type RekorEntry,
  type SigstoreVerifier,
} from "../src/signature.js";

const FIX = (rel: string) =>
  resolve(__dirname, "../../../../fixtures/mda", rel);

const SIGSTORE_SIGNER = "sigstore-oidc:https://accounts.google.com";

const GOOGLE_POLICY = {
  version: 1,
  trustedSigners: [
    {
      type: "sigstore-oidc",
      issuer: "https://accounts.google.com",
      subject: "releases@snoai.com",
    },
  ],
  rekor: { url: "https://rekor.sigstore.dev" },
} as const;

const DID_WEB_POLICY = {
  version: 1,
  trustedSigners: [{ type: "did-web", domain: "tools.example.com" }],
} as const;

const MinimalSchema = z.object({
  name: z.string(),
  description: z.string(),
  metadata: z
    .object({
      mda: z
        .object({
          version: z.string().optional(),
          "doc-id": z.string().optional(),
          tags: z.array(z.string()).optional(),
        })
        .optional(),
    })
    .optional(),
  requires: z
    .object({
      network: z
        .union([z.literal("none"), z.literal("local"), z.literal("public"), z.array(z.string())])
        .optional(),
    })
    .optional(),
  integrity: z
    .object({ algorithm: z.string(), digest: z.string() })
    .optional(),
  signatures: z.array(z.unknown()).optional(),
});

describe("loadMdaSource — Stage A → D + G (no signatures, no requires)", () => {
  it("loads a minimal source-mode .mda file", async () => {
    const cfg = await loadMdaSource(FIX("valid/01-minimal.mda"), MinimalSchema);
    expect(cfg.name).toBe("minimal-config");
  });

  it("rejects YAML parse errors with frontmatter-yaml-parse-error", async () => {
    try {
      await loadMdaSource(FIX("invalid/10-yaml-parse-error.mda"), MinimalSchema);
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.FrontmatterYamlParseError);
    }
  });

  it("rejects integrity mismatch with integrity-mismatch", async () => {
    try {
      await loadMdaSource(FIX("invalid/11-integrity-mismatch.mda"), MinimalSchema, {
        verifyIntegrity: true,
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.IntegrityMismatch);
    }
  });

  it("rejects signature-digest-mismatch via Stage C", async () => {
    try {
      await loadMdaSource(FIX("invalid/12-signature-digest-mismatch.mda"), MinimalSchema);
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.SignatureDigestMismatch);
    }
  });

  it("reports signatures-without-integrity before generic schema errors", async () => {
    const src = `---
name: signatures-without-integrity
description: signature metadata without an integrity anchor
signatures:
  - signer: "sigstore-oidc:https://accounts.google.com"
    key-id: "fulcio:test"
    payload-digest: "sha256:9697448b6f3f88b71870dd5b608999ade717f73d4eebf67f02ac03dfe177a37e"
    algorithm: ecdsa-p256
    signature: "FIXTUREONLY=="
    rekor-log-id: "c0d23b6c4f200000000000000000000000000000000000000000000000000000"
    rekor-log-index: 87654321
---
`;
    try {
      await loadMdaSourceFromBytes(new TextEncoder().encode(src), MinimalSchema);
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.SignaturesWithoutIntegrity);
    }
  });

  it("verifies integrity when verifyIntegrity=true", async () => {
    const cfg = await loadMdaSource(FIX("valid/02-with-integrity.mda"), MinimalSchema, {
      verifyIntegrity: true,
    });
    expect(cfg.integrity?.algorithm).toBe("sha256");
  });

  it("rejects verifyIntegrity=true when integrity is absent", async () => {
    try {
      await loadMdaSource(FIX("valid/01-minimal.mda"), MinimalSchema, {
        verifyIntegrity: true,
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.SchemaViolation);
    }
  });

  it("Stage G: surfaces project-schema-violation when consumer Zod fails", async () => {
    const NarrowSchema = z.object({
      name: z.literal("does-not-match"),
      description: z.string(),
    });
    try {
      await loadMdaSource(FIX("valid/01-minimal.mda"), NarrowSchema);
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.ProjectSchemaViolation);
    }
  });
});

describe("loadMdaSource — Stage F (requires.network)", () => {
  it("accepts when network host is in allowedNetworks", async () => {
    const cfg = await loadMdaSource(
      FIX("valid/04-with-requires-network.mda"),
      MinimalSchema,
      {
        enforceRequires: true,
        allowedNetworks: ["api.openai.com"],
      },
    );
    expect(cfg.requires?.network).toContain("api.openai.com");
  });

  it("accepts when operator allow-list has a matching host glob", async () => {
    const cfg = await loadMdaSource(
      FIX("valid/04-with-requires-network.mda"),
      MinimalSchema,
      {
        enforceRequires: true,
        allowedNetworks: ["*.openai.com"],
      },
    );
    expect(cfg.requires?.network).toContain("api.openai.com");
  });

  it("rejects invalid requires.network shape in Stage F", async () => {
    const { loadMdaSourceFromBytes } = await import("../src/loader.js");
    const src = `---
name: bad-network-shape
description: requires.network value is invalid
requires:
  network: 7
---
`;
    try {
      await loadMdaSourceFromBytes(new TextEncoder().encode(src), MinimalSchema, {
        enforceRequires: true,
      });
      throw new Error("expected throw");
    } catch (e) {
      const err = e as MdaConfigError;
      expect(err.category).toBe(ErrorCategory.RequiresNotSatisfied);
      expect(err.details).toMatchObject({
        key: "network",
        reason: "invalid-shape",
        got: 7,
      });
    }
  });

  it("rejects metadata service addresses for requires.network=local", async () => {
    const src = `---
name: local-network
description: requires local network
requires:
  network: local
---
`;
    try {
      await loadMdaSourceFromBytes(new TextEncoder().encode(src), MinimalSchema, {
        enforceRequires: true,
        allowedNetworks: ["169.254.169.254"],
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.RequiresNotSatisfied);
    }
  });

  it("rejects when host is not in allowedNetworks", async () => {
    try {
      await loadMdaSource(FIX("invalid/15-network-violation.mda"), MinimalSchema, {
        enforceRequires: true,
        allowedNetworks: ["api.openai.com"],
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.RequiresNotSatisfied);
    }
  });
});

// ────────────────── Stage E: Sigstore signature verification ──────────────────

/** A mock Rekor client that returns the supplied entry for any (id, idx). */
function mockRekor(entry: RekorEntry | null): RekorClient {
  return {
    url: "https://rekor.sigstore.dev",
    async fetchEntry() {
      return entry;
    },
  };
}

const fakeVerifier: SigstoreVerifier = {
  async verify() {
    return {
      identity: {
        issuer: "https://accounts.google.com",
        subjectAlternativeName: "releases@snoai.com",
      },
    };
  },
};

async function fixtureBytes(rel: string): Promise<Uint8Array> {
  const text = await readFile(FIX(rel), "utf8");
  return new TextEncoder().encode(text);
}

function quotedField(source: string, field: string): string {
  const match = source.match(new RegExp(`${field}: "([^"]+)"`, "u"));
  if (!match?.[1]) throw new Error(`missing ${field}`);
  return match[1];
}

function numericField(source: string, field: string): number {
  const match = source.match(new RegExp(`${field}: (\\d+)`, "u"));
  if (!match?.[1]) throw new Error(`missing ${field}`);
  return Number(match[1]);
}

function optionalQuotedField(source: string, field: string): string | undefined {
  return source.match(new RegExp(`${field}: "([^"]+)"`, "u"))?.[1];
}

async function didWebSource(domain = "tools.example.com"): Promise<Uint8Array> {
  const src = new TextDecoder().decode(
    await fixtureBytes("valid/03-sigstore-signed.mda"),
  ).replace(SIGSTORE_SIGNER, `did-web:${domain}`)
    .replace(/    rekor-log-id: "[^"]+"\n    rekor-log-index: \d+\n/u, "");
  return new TextEncoder().encode(src);
}

async function fixtureRekorEntry(
  rel: string,
  overrides: Partial<RekorEntry> = {},
): Promise<RekorEntry> {
  const source = await readFile(FIX(rel), "utf8");
  const digest = quotedField(source, "digest");
  const payloadType = optionalQuotedField(source, "payload-type") ?? DEFAULT_PAYLOAD_TYPE;
  const payload = Buffer.from(
    JSON.stringify({ algorithm: "sha256", digest }),
  ).toString("base64");
  const entry: RekorEntry = {
    kind: "dsse-v0.0.1",
    logId: quotedField(source, "rekor-log-id"),
    logIndex: numericField(source, "rekor-log-index"),
    inclusionVerified: true,
    certificatePem: "",
    dsseEnvelope: {
      payloadType,
      payload,
      signatures: [
        {
          sig: quotedField(source, "signature"),
          keyid: quotedField(source, "key-id"),
        },
      ],
    },
  };
  return { ...entry, ...overrides };
}

describe("loadMdaSource — Stage E (Sigstore signatures)", () => {
  it("rejects verifySignatures=true when signatures are absent", async () => {
    try {
      await loadMdaSource(FIX("valid/02-with-integrity.mda"), MinimalSchema, {
        verifySignatures: true,
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.TrustPolicyViolation);
    }
  });

  it("rejects verifySignatures=true without the Sigstore verifier hook", async () => {
    try {
      await loadMdaSourceFromBytes(
        await fixtureBytes("valid/03-sigstore-signed.mda"),
        MinimalSchema,
        {
          verifySignatures: true,
          trustPolicy: GOOGLE_POLICY,
          rekorClient: mockRekor(null),
        },
      );
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.TrustPolicyViolation);
    }
  });

  it("accepts a sigstore-signed fixture under a passing verifier + matching trust policy", async () => {
    const cfg = await loadMdaSourceFromBytes(
      await fixtureBytes("valid/03-sigstore-signed.mda"),
      MinimalSchema,
      {
        verifyIntegrity: true,
        verifySignatures: true,
        trustPolicy: {
          ...GOOGLE_POLICY,
        },
        rekorClient: mockRekor(await fixtureRekorEntry("valid/03-sigstore-signed.mda")),
        sigstoreVerifier: fakeVerifier,
      },
    );
    expect(cfg.integrity?.digest).toMatch(/^sha256:/);
  });

  it("rejects when Rekor entry kind is not dsse-v0.0.1", async () => {
    try {
      await loadMdaSourceFromBytes(
        await fixtureBytes("invalid/14-rekor-entry-type-wrong.mda"),
        MinimalSchema,
        {
          verifyIntegrity: true,
          verifySignatures: true,
          trustPolicy: {
            ...GOOGLE_POLICY,
          },
          rekorClient: mockRekor(
            await fixtureRekorEntry("invalid/14-rekor-entry-type-wrong.mda", {
              kind: "hashedrekord-v0.0.1",
            }),
          ),
          sigstoreVerifier: fakeVerifier,
        },
      );
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.RekorEntryTypeMismatch);
    }
  });

  it("rejects Rekor entries without explicit coordinate and inclusion binding", async () => {
    for (const override of [
      { logId: undefined },
      { logIndex: undefined },
      { inclusionVerified: undefined },
    ] satisfies Partial<RekorEntry>[]) {
      try {
        await loadMdaSourceFromBytes(
          await fixtureBytes("valid/03-sigstore-signed.mda"),
          MinimalSchema,
          {
            verifyIntegrity: true,
            verifySignatures: true,
            trustPolicy: GOOGLE_POLICY,
            rekorClient: mockRekor(
              await fixtureRekorEntry("valid/03-sigstore-signed.mda", override),
            ),
            sigstoreVerifier: fakeVerifier,
          },
        );
        throw new Error("expected throw");
      } catch (e) {
        expect((e as MdaConfigError).category).toBe(ErrorCategory.RekorInclusionFailure);
      }
    }
  });

  it("rejects when cert identity does not match the trust policy", async () => {
    try {
      await loadMdaSourceFromBytes(
        await fixtureBytes("valid/03-sigstore-signed.mda"),
        MinimalSchema,
        {
          verifyIntegrity: true,
          verifySignatures: true,
          trustPolicy: {
            ...GOOGLE_POLICY,
            trustedSigners: [{
              type: "sigstore-oidc",
              issuer: "https://accounts.google.com",
              subject: "someone-else@example.com",
            }],
          },
          rekorClient: mockRekor(await fixtureRekorEntry("valid/03-sigstore-signed.mda")),
          sigstoreVerifier: fakeVerifier,
        },
      );
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.NoTrustedSignature);
    }
  });

  it("rejects did-web trusted-runtime when didWebVerifier is absent", async () => {
    try {
      await loadMdaSourceFromBytes(await didWebSource(), MinimalSchema, {
        trustedRuntime: true,
        trustPolicy: DID_WEB_POLICY,
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.TrustPolicyViolation);
    }
  });

  it("does not invoke did-web verifier for an untrusted domain", async () => {
    const verifier: DidWebVerifier = {
      async verify() {
        throw new Error("untrusted did:web domain must not reach verifier");
      },
    };
    try {
      await loadMdaSourceFromBytes(await didWebSource("evil.example.com"), MinimalSchema, {
        trustedRuntime: true,
        trustPolicy: DID_WEB_POLICY,
        didWebVerifier: verifier,
      });
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.NoTrustedSignature);
    }
  });
});
