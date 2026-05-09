# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x     | Yes       |
| 1.x     | No        |

## Reporting a Vulnerability

If you discover a security vulnerability in LLMix, do not open a public GitHub
issue.

Report it privately via [GitHub Security Advisories](../../security/advisories/new)
or email the maintainers directly.

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Affected versions

You can expect an acknowledgment within 48 hours and a resolution timeline
within 14 days for confirmed vulnerabilities.

## Scope

LLMix handles API keys and routes LLM provider traffic. Security-relevant areas
include:

- **Key pool** (`key_pool.py`, `key-pool.ts`) — API key rotation and dead-key
  marking. Keys are held in memory; they are never written to disk by the
  library.
- **Kill switch** — filesystem-based state stored in `LLMIX_STATE_DIR`,
  `XDG_STATE_HOME/llmix`, or `~/.local/state/llmix` by default. Directory
  permissions are the caller's responsibility.
- **File lock** (`resilience.py`, `resilience.ts`) — cross-process lock file.
  Uses `proper-lockfile` in TypeScript and `fcntl` in Python.
- **Provider dispatch** — the dispatch callback is caller-supplied. The
  library does not validate or sanitize provider responses.
- **Cache keys** — SHA-256 of canonical JSON with the `llmix:resp:` prefix.
  Cache contents are stored as-is, including `<think>` blocks. Redis L2 cache
  security is the caller's responsibility.
