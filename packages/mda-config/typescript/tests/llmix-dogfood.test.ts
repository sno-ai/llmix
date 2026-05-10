/**
 * LLMix dogfood (PRD §10 Appendix A acceptance):
 *   tests/fixtures/sample_preset.mda loads + integrity-verifies + enforces
 *   requires.network end-to-end against an LLMix-shaped Zod schema. Sigstore
 *   crypto is injected through a verifier hook; Stage E mechanics are covered here.
 */

import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { loadMdaSourceFromBytes } from "../src/loader.js";
import { DEFAULT_PAYLOAD_TYPE, type RekorClient, type SigstoreVerifier } from "../src/signature.js";

const FIX = resolve(__dirname, "fixtures/sample_preset.mda");

// Minimal LLMix Zod shape (mirrors apps/llmix-cli/src/yaml-loader.ts in spirit).
const LLMixPresetSchema = z.object({
  name: z.string(),
  description: z.string(),
  requires: z
    .object({ network: z.array(z.string()).optional() })
    .optional(),
  metadata: z.object({
    mda: z
      .object({
        version: z.string().optional(),
        "doc-id": z.string().optional(),
        tags: z.array(z.string()).optional(),
      })
      .optional(),
    "snoai-llmix": z.object({
      common: z.object({
        model: z.string(),
        provider: z.string(),
        temperature: z.number().optional(),
        maxOutputTokens: z.number().optional(),
      }),
      providerOptions: z.record(z.string(), z.unknown()).optional(),
      caching: z.object({ strategy: z.string() }).optional(),
    }),
  }),
  integrity: z.object({ algorithm: z.string(), digest: z.string() }).optional(),
  signatures: z.array(z.unknown()).optional(),
});

describe("LLMix dogfood — sample_preset.mda", () => {
  it("loads + integrity-verifies + signature-verifies + enforces requires.network", async () => {
    const source = await readFile(FIX, "utf8");
    const digest = source.match(/digest: "([^"]+)"/u)?.[1];
    const signature = source.match(/signature: "([^"]+)"/u)?.[1];
    const keyid = source.match(/key-id: "([^"]+)"/u)?.[1];
    const logId = source.match(/rekor-log-id: "([^"]+)"/u)?.[1];
    const logIndex = Number(source.match(/rekor-log-index: ([0-9]+)/u)?.[1]);
    if (!digest || !signature || !keyid || !logId || !Number.isSafeInteger(logIndex)) {
      throw new Error("fixture is missing signature fields");
    }
    const rekorClient: RekorClient = {
      async fetchEntry() {
        return {
          kind: "dsse-v0.0.1",
          logId,
          logIndex,
          inclusionVerified: true,
          certificatePem: "",
          dsseEnvelope: {
            payloadType: DEFAULT_PAYLOAD_TYPE,
            payload: Buffer.from(
              JSON.stringify({ algorithm: "sha256", digest }),
            ).toString("base64"),
            signatures: [{ sig: signature, keyid }],
          },
        };
      },
    };
    const verifier: SigstoreVerifier = {
      async verify() {
        return {
          identity: {
            issuer: "https://accounts.google.com",
            subjectAlternativeName: "releases@snoai.com",
          },
        };
      },
    };
    const cfg = await loadMdaSourceFromBytes(new TextEncoder().encode(source), LLMixPresetSchema, {
      verifyIntegrity: true,
      verifySignatures: true,
      enforceRequires: true,
      allowedNetworks: ["api.openai.com"],
      trustPolicy: {
        version: 1,
        trustedSigners: [
          {
            type: "sigstore-oidc",
            issuer: "https://accounts.google.com",
            subject: "releases@snoai.com",
          },
        ],
        rekor: { url: "https://rekor.sigstore.dev" },
      },
      rekorClient,
      sigstoreVerifier: verifier,
    });
    expect(cfg.name).toBe("gpt5-mini-fast");
    expect(cfg.metadata["snoai-llmix"].common.model).toBe("gpt-5-mini");
    expect(cfg.requires?.network).toEqual(["api.openai.com"]);
  });
});
