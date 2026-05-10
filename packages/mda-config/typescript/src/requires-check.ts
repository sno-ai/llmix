/**
 * MDA §10-3.3 / §10-4 — `requires.network` enforcement.
 *
 * v1.0 enforces only `requires.network` per PRD §2 (other standard `requires`
 * keys are passed through to the consumer's Zod layer, not enforced here).
 * Unknown keys are ignored without error per §10-4.
 */

import { ErrorCategory, MdaConfigError } from "./errors.js";
import { isIP } from "node:net";

/** Source-mode `requires` block (top level per §02 + PRD §3.2). */
export interface RequiresBlock {
  network?: "none" | "local" | "public" | string[];
  // Other standard keys (runtime, tools, packages, model, cost-hints) are
  // intentionally untyped here — v1.0 does not enforce them (PRD §2).
  [key: string]: unknown;
}

/** §10-3.3 — `network` value forms. */
export type NetworkRequirement = "none" | "local" | "public" | string[];

/** Operator-supplied environment for §10-4 enforcement. */
export interface RequiresEnvironment {
  /** Hosts the operator permits the artifact to contact. */
  allowedNetworks?: string[];
}

/**
 * §10-4 — enforce `requires.network` against the operator's allow-list.
 *
 * - `none`: any non-empty `allowedNetworks` is irrelevant; always satisfied.
 * - `local`: requires the operator to NOT permit any non-loopback hosts. We
 *   approximate by accepting any environment whose `allowedNetworks` is empty
 *   or contains only loopback / RFC1918 names. Operators who need richer
 *   semantics layer their own policy.
 * - `public`: requires the operator to grant unrestricted public access; we
 *   model that as `allowedNetworks` containing the wildcard `"*"`.
 * - `string[]`: every entry MUST appear in `allowedNetworks` (literal match
 *   or wildcard `"*"`).
 */
export function enforceRequires(
  requires: RequiresBlock | undefined,
  env: RequiresEnvironment,
): void {
  if (!requires) return;
  const network = requires.network;
  if (network === undefined) return;

  const allowed = new Set(env.allowedNetworks ?? []);
  const wildcard = allowed.has("*");

  if (network === "none") return;

  if (network === "local") {
    // Best-effort: any allow-list entry that is not loopback/RFC1918 fails.
    for (const host of allowed) {
      if (!isLocalHost(host)) {
        throw new MdaConfigError(
          ErrorCategory.RequiresNotSatisfied,
          "requires.network=local but operator permits non-local host",
          { key: "network", host },
        );
      }
    }
    return;
  }

  if (network === "public") {
    if (!wildcard) {
      throw new MdaConfigError(
        ErrorCategory.RequiresNotSatisfied,
        "requires.network=public but operator does not grant wildcard '*'",
        { key: "network" },
      );
    }
    return;
  }

  if (Array.isArray(network)) {
    if (!network.every((host) => typeof host === "string" && host.length > 0)) {
      throw invalidNetworkShape(network);
    }
    if (wildcard) return;
    for (const host of network) {
      if (!isNetworkAllowed(host, allowed)) {
        throw new MdaConfigError(
          ErrorCategory.RequiresNotSatisfied,
          `requires.network host '${host}' not in operator allow-list`,
          { key: "network", host, allowed: [...allowed] },
        );
      }
    }
    return;
  }

  throw invalidNetworkShape(network);
}

function invalidNetworkShape(got: unknown): never {
  throw new MdaConfigError(
    ErrorCategory.RequiresNotSatisfied,
    "requires.network has an invalid shape",
    { key: "network", reason: "invalid-shape", got },
  );
}

function isNetworkAllowed(requiredHost: string, allowed: Set<string>): boolean {
  if (allowed.has(requiredHost)) return true;
  for (const pattern of allowed) {
    if (hostMatchesPattern(requiredHost, pattern)) return true;
  }
  return false;
}

function hostMatchesPattern(host: string, pattern: string): boolean {
  if (!pattern.includes("*")) return host === pattern;
  const escaped = pattern
    .replace(/[.+?^${}()|[\]\\]/gu, "\\$&")
    .replace(/\*/gu, "[^.]+");
  return new RegExp(`^${escaped}$`, "u").test(host);
}

function isLocalHost(host: string): boolean {
  const normalized = host.replace(/\.+$/u, "").toLowerCase();
  if (
    normalized === "localhost" ||
    normalized.endsWith(".localhost") ||
    normalized.endsWith(".local") ||
    normalized.endsWith(".internal")
  ) {
    return true;
  }
  if (isIP(normalized) === 4) {
    if (normalized === "127.0.0.1") return true;
    if (/^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/u.test(normalized)) return true;
    if (/^192\.168\.\d{1,3}\.\d{1,3}$/u.test(normalized)) return true;
    if (/^172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}$/u.test(normalized)) {
      return true;
    }
  }
  if (isIP(normalized) === 6) {
    return normalized === "::1" || /^f[cd][0-9a-f]{2}:/u.test(normalized);
  }
  return false;
}
