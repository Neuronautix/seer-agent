<!-- Thanks for contributing! Please fill out the sections below. -->

## Summary

<!-- One or two sentences describing what this PR does and why. -->

## Changes

<!-- Bulleted list of the concrete changes in this PR. -->

-
-

## Testing

<!-- Which test files did you run? Paste a brief summary of results. -->

- [ ] `.venv/bin/python scripts/test_pipeline.py`
- [ ] `.venv/bin/python scripts/test_supervisor.py`
- [ ] `.venv/bin/python scripts/test_alarm_runtime.py`
- [ ] `.venv/bin/python deploy/nanobot/test_tool_layer.py` (if MCP tools touched)

## Checklist

- [ ] Tests pass locally.
- [ ] No secrets, personal identifiers, or hardcoded `/home/<user>/` paths.
- [ ] Docs updated where behavior changes (README, OPERATIONS, CLAUDE.md).
- [ ] Read-only/local-first invariants preserved (see CLAUDE.md).
