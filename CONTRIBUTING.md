# Contributing

Thanks for your interest in Sovereign Sensor Agent. This document describes
how to propose changes.

## Ground Rules

Before contributing, please read [`CLAUDE.md`](./CLAUDE.md). It contains the
project's architectural invariants. The most important ones:

- **The Python pipeline is the only source of truth.** LLMs and HTTP clients
  are observers, not writers.
- **Read-only tools stay read-only.** Anything under `workspace/tools/` or
  `deploy/nanobot/mcp_server.py` must never write to logs, schemas, or storage.
- **No cloud egress from the deterministic pipeline.** Network calls belong to
  the optional Nanobot/WhatsApp layer, never to `scripts/`.
- **Schema changes are breaking changes.** Updating
  `schemas/sensor-observation-v1.json` requires updates to
  `ontology_guard.py`, `build_observation.py`, and `scripts/test_pipeline.py`.

If your change conflicts with any of these, open an issue first to discuss the
design.

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running Tests

There is no CI. Please run the relevant test files locally before opening a
pull request:

```bash
.venv/bin/python scripts/test_pipeline.py
.venv/bin/python scripts/test_supervisor.py
.venv/bin/python scripts/test_alarm_runtime.py
```

If you touch the MCP tool layer, also run:

```bash
.venv/bin/python deploy/nanobot/test_tool_layer.py
```

## Pull Request Checklist

- [ ] Branch off `main`; keep commits focused.
- [ ] Tests pass locally (see above).
- [ ] No personal identifiers, secrets, or hardcoded `/home/<username>/` paths
      in the diff.
- [ ] Documentation updated where behavior changes (README, OPERATIONS, or
      CLAUDE.md).
- [ ] Commit messages describe the *why* and reference the component changed
      (e.g., `Fix pressure parsing in read_serial.py`).

## Reporting Issues

Use GitHub Issues for bug reports and feature requests. For security
vulnerabilities, follow [`SECURITY.md`](./SECURITY.md) instead — please do not
file public issues for security problems.

## Code Style

- Python 3.11+, standard library `unittest` (no pytest).
- Keep modules small and single-purpose; prefer existing utilities over new
  abstractions.
- No new top-level dependencies without discussion.
- Comments only where the *why* is non-obvious. Don't restate what the code
  already says.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](./LICENSE).
