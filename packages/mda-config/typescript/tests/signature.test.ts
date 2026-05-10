/**
 * §09-3.1 PAE construction unit tests.
 * Spec alignment matrix (PRD §5) row "DSSE PAE envelope construction" must be
 * exercised by at least one fixture/test; this file owns that coverage along
 * with the "payload-type optional with default" row.
 */

import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";
import {
  constructDssePae,
  DEFAULT_PAYLOAD_TYPE,
  officialSigstoreVerifier,
  type RekorClient,
  type SigstoreVerifier,
  verifySignatures,
} from "../src/signature.js";

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

describe("§09-3.1 DSSE PAE envelope", () => {
  it("emits the literal recipe: 'DSSEv1 SP <typeLen> SP <type> SP <payloadLen> SP <payload>'", () => {
    const payloadType = "application/vnd.mda.integrity+json";
    const payloadStr = '{"a":1}';
    const bytes = constructDssePae(payloadType, new TextEncoder().encode(payloadStr));
    const expected =
      `DSSEv1 ${payloadType.length} ${payloadType} ${payloadStr.length} ${payloadStr}`;
    expect(new TextDecoder().decode(bytes)).toBe(expected);
  });

  it("uses the default payload-type when signatures[i].payload-type is absent", async () => {
    let captured: Uint8Array | undefined;
    const verifier: SigstoreVerifier = {
      async verify(_entry, _signature, paeBytes) {
        captured = paeBytes;
        return {
          identity: {
            issuer: "https://accounts.google.com",
            subjectAlternativeName: "releases@snoai.com",
          },
        };
      },
    };
    const integrity = {
      algorithm: "sha256" as const,
      digest:
        "sha256:a4f9c0d2e8b3a16e9c01b8f3d2a5c7b14e9f8a3d6c2b1e7f0a8d4c3b9e2f1a05",
    };
    const expectedPayload = Buffer.from(JSON.stringify(integrity)).toString("base64");
    const rekorClient: RekorClient = {
      async fetchEntry(rekorUrl) {
        expect(rekorUrl).toBe("https://rekor.sigstore.dev");
        return {
          kind: "dsse-v0.0.1",
          logId: "logid",
          logIndex: 1,
          inclusionVerified: true,
          certificatePem: "",
          dsseEnvelope: {
            payloadType: DEFAULT_PAYLOAD_TYPE,
            payload: expectedPayload,
            signatures: [{ sig: "MEUC=", keyid: "fulcio:abc" }],
          },
        };
      },
    };
    await verifySignatures(
      [
        {
          signer: "sigstore-oidc:https://accounts.google.com",
          "key-id": "fulcio:abc",
          "payload-digest": integrity.digest,
          algorithm: "ecdsa-p256",
          signature: "MEUC=",
          "rekor-log-id": "logid",
          "rekor-log-index": 1,
          // payload-type intentionally omitted
        },
      ],
      integrity,
      GOOGLE_POLICY,
      { rekorClient, sigstoreVerifier: verifier },
    );
    expect(captured).toBeDefined();
    const decoded = new TextDecoder().decode(captured!);
    // §09-2 default — type slot must equal application/vnd.mda.integrity+json.
    expect(decoded.startsWith(`DSSEv1 ${DEFAULT_PAYLOAD_TYPE.length} ${DEFAULT_PAYLOAD_TYPE} `))
      .toBe(true);
  });

  it("rejects an empty signatures array", async () => {
    const rekorClient: RekorClient = {
      async fetchEntry() {
        return null;
      },
    };
    const verifier: SigstoreVerifier = {
      async verify() {
        throw new Error("unreachable");
      },
    };
    await expect(
      verifySignatures(
        [],
        {
          algorithm: "sha256",
          digest:
            "sha256:a4f9c0d2e8b3a16e9c01b8f3d2a5c7b14e9f8a3d6c2b1e7f0a8d4c3b9e2f1a05",
        },
        GOOGLE_POLICY,
        { rekorClient, sigstoreVerifier: verifier },
      ),
    ).rejects.toMatchObject({ category: "missing-required-signature" });
  });

  it("requires raw bundles for the official Sigstore verifier", async () => {
    const integrity = {
      algorithm: "sha256" as const,
      digest:
        "sha256:a4f9c0d2e8b3a16e9c01b8f3d2a5c7b14e9f8a3d6c2b1e7f0a8d4c3b9e2f1a05",
    };
    const expectedPayload = Buffer.from(JSON.stringify(integrity)).toString("base64");
    const rekorClient: RekorClient = {
      async fetchEntry() {
        return {
          kind: "dsse-v0.0.1",
          logId: "logid",
          logIndex: 1,
          inclusionVerified: true,
          certificatePem: "",
          dsseEnvelope: {
            payloadType: DEFAULT_PAYLOAD_TYPE,
            payload: expectedPayload,
            signatures: [{ sig: "MEUC=", keyid: "fulcio:abc" }],
          },
        };
      },
    };
    await expect(
      verifySignatures(
        [
          {
            signer: "sigstore-oidc:https://accounts.google.com",
            "key-id": "fulcio:abc",
            "payload-digest": integrity.digest,
            algorithm: "ecdsa-p256",
            signature: "MEUC=",
            "rekor-log-id": "logid",
            "rekor-log-index": 1,
          },
        ],
        integrity,
        GOOGLE_POLICY,
        { rekorClient, sigstoreVerifier: officialSigstoreVerifier() },
      ),
    ).rejects.toMatchObject({
      category: "signature-verification-failure",
      details: { cause: "officialSigstoreVerifier requires RekorEntry.rawBundle" },
    });
  });
});
