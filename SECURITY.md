# Security Policy

Sovereign Sensor Agent is a local-first system that ingests sensor data, exposes
a read-only HTTP API, and (optionally) bridges to WhatsApp through a constrained
LLM tool layer. This document describes the project's security model and how to
report vulnerabilities.

## Supported Versions

This project follows rolling releases on the `main` branch. Security fixes are
applied to `main` only; users running older commits are expected to update.

## Reporting a Vulnerability

Please report security issues **privately** rather than by opening a public
GitHub issue. Use one of the following channels:

- Open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/repository-security-advisories/about-repository-security-advisories)
  on this repository (preferred).
- Or email the maintainers via the address listed in the repository's
  GitHub profile.

When reporting, please include:

- A description of the issue and its potential impact.
- Steps to reproduce, including configuration and version (commit SHA).
- Any logs, proof-of-concept, or suggested remediation.

We aim to acknowledge reports within 5 business days and to provide a fix or
mitigation plan within 30 days, depending on severity.

## Threat Model & Hardening Notes

The project assumes:

- **The host is trusted.** Filesystem access on the host implies access to
  observation logs, threshold configuration, WhatsApp auth state, and any
  credentials in `deploy/nanobot/nanobot.env`.
- **The Python pipeline is the only writer.** LLM tools and the HTTP API are
  read-only. Do not add write paths to `workspace/tools/` or LLM-facing code.
- **The HTTP API binds to localhost by default.** Do not expose it to the
  public internet without an authenticating reverse proxy.

### Sensitive files

These files contain credentials or identifiers and must not be committed or
exposed:

| File | Sensitivity |
|------|-------------|
| `deploy/nanobot/nanobot.env` | API keys, admin password, WhatsApp IDs |
| `deploy/nanobot/whatsapp-auth/` | WhatsApp session credentials |
| `threshold-config.json` | Operational state (less sensitive) |
| `logs/*.jsonl`, `logs/*.json` | Sensor data; treat per local policy |
| `google-tasks-token.json` | OAuth tokens, if Google Tasks integration is enabled |

All of the above are excluded by `.gitignore`. Verify before any commit.

### `SSA_ADMIN_PASSWORD`

The admin command path (used by WhatsApp messages prefixed with
`@ssa <password> ...`) is gated by `SSA_ADMIN_PASSWORD`. The default value
shipped in `nanobot.env.example` is the literal placeholder `CHANGE_ME` —
**you must set this to a strong, unguessable token before enabling the WhatsApp
bridge**. The pre-flight check (`scripts/check_env.py`) emits a warning if the
value is left at the default.

Treat this token like any other admin credential: rotate periodically, never
commit it, and limit who has access to the host.

### WhatsApp sender allowlist

`NANOBOT_WHATSAPP_ALLOW_FROM` restricts which sender IDs the agent will accept
commands from. Leaving this empty while `NANOBOT_ENABLE_WHATSAPP=true` causes
all inbound messages to be dropped. Always set it explicitly to the WhatsApp
ID(s) you trust.

### Network exposure

If you expose the HTTP API beyond localhost:

- Front it with TLS termination and authentication (e.g., a reverse proxy that
  enforces basic auth or mTLS).
- The API itself is read-only by design, but freshness data and raw
  observations may still be sensitive in your environment.

### Updating dependencies

The Python stack is minimal (`jsonschema`, `mcp`, `nanobot-ai`). Pull updates
through `pip install -r requirements.txt --upgrade` and re-run the test suite
before deploying.

## Out of Scope

- Physical attacks on the Arduino hardware or its serial link.
- Vulnerabilities in upstream dependencies (`mcp`, `nanobot-ai`, etc.) — please
  report those to their respective projects.
- Misconfigurations on the host (file permissions, exposed ports) that fall
  outside the project's defaults.
