# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does not yet follow strict semantic versioning; treat `main` as
the rolling release.

## [Unreleased]

### Added

- Apache 2.0 LICENSE.
- `SECURITY.md` describing the threat model and vulnerability disclosure
  process.
- `CONTRIBUTING.md` covering setup, testing, and pull-request guidelines.
- `.github/` issue and pull-request templates.
- `arduino/sense-rev2/` placeholder for the reference Arduino sketch and
  wiring notes.
- README `Demo` section with placeholders for hardware photo and WhatsApp /
  CLI screenshots; `docs/images/` directory with a capture and redaction
  checklist.
- Three Mermaid architecture diagrams (data flow, trust boundary, deployment
  topology) replacing the previous one-line text arrow.
- Runtime warnings emitted to stderr by `scripts/alarm_runtime.py`,
  `scripts/api_server.py`, and `deploy/nanobot/start_nanobot.sh` when
  `SSA_ADMIN_PASSWORD` is unset or still set to the `CHANGE_ME` placeholder.

### Changed

- Replaced hardcoded `/home/<user>/sovereign-sensor-agent` paths in
  `README.md` and the systemd unit files with the configurable
  `/opt/sovereign-sensor-agent` install location and a `${SSA_USER}`
  placeholder.
- Default `SSA_ADMIN_PASSWORD` is now the explicit placeholder `CHANGE_ME`
  instead of `8888`, making it obvious the value must be configured before
  enabling the WhatsApp bridge.
- `OPERATIONS.md`: removed machine-specific state notes ("the current machine
  does not have Node yet", "what is still missing") and rephrased remaining
  prerequisites generically; updated WhatsApp admin-command examples to use
  the canonical `@ssa <admin-password> ...` prefix.

## [0.1.0] - Initial public release

Initial open-source publication of the Sovereign Sensor Agent: a local-first,
privacy-preserving sensor ingestion pipeline with a read-only HTTP API and
optional WhatsApp/LLM observer layer.
