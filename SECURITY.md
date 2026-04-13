# Security Notes

This document covers what you must address before exposing this system in any production or shared environment.

---

## Before You Deploy

### 1. Change the admin password

`SSA_ADMIN_PASSWORD` defaults to a well-known value in `nanobot.env.example`. Any deployment that does not override this is trivially exploitable via the `/webhook` endpoint.

Set a strong value in `deploy/nanobot/nanobot.env`:

```bash
SSA_ADMIN_PASSWORD=<choose-a-strong-password>
```

### 2. Lock down the WhatsApp sender allowlist

`NANOBOT_WHATSAPP_ALLOW_FROM` should be an explicit comma-separated list of WhatsApp sender IDs. Never set it to `*`. The startup script refuses wildcard mode unless you also set `NANOBOT_WHATSAPP_ALLOW_ALL=true`.

### 3. The webhook endpoint has no authentication

`POST /webhook` on port 8080 accepts commands from any caller that knows the admin password. Before exposing the port outside localhost:

- Add webhook authentication or signature verification.
- Restrict access with a firewall rule, reverse proxy, or tunnel that enforces authentication upstream.

### 4. Keep `nanobot.env` off version control

`nanobot.env` is already listed in `.gitignore`, but confirm it has never been committed:

```bash
git log --all --full-history -- deploy/nanobot/nanobot.env
```

If it appears in history, rotate any credentials it contained and remove it from git history before publishing.

### 5. Review the WhatsApp bridge trust boundary

Nanobot's workspace isolation prevents it from reading the serial port or the repository root, but the installed version includes built-in filesystem tools scoped to its configured workspace. Verify the workspace path in `deploy/nanobot/start_nanobot.sh` is limited to what the agent genuinely needs.

---

## Reporting a Vulnerability

If you discover a security issue, please open a GitHub issue with the label `security` or contact the maintainer directly before public disclosure.
