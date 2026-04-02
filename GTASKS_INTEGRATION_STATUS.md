# Google Tasks Integration Status

Last updated: 2026-04-02

## Current Stage

- [x] Repository state verified
- [x] Existing Nanobot and sensor integration points reviewed
- [x] Target architecture defined
- [ ] Google Tasks implementation added
- [ ] End-to-end verification completed

The repository is currently in the design and planning stage for Google Tasks support. No GTasks implementation files have been added yet, and the working tree is clean.

## Agreed Plan

- [x] Confirm that there is no existing Google Tasks integration to extend directly
- [x] Choose the integration shape: a separate Google Tasks MCP server, not changes inside the sensor source-of-truth pipeline
- [x] Keep sensor ingestion and read-only observation tooling isolated from task-writing behavior
- [ ] Add a Google Tasks client and MCP server under `deploy/nanobot/`
- [ ] Add an OAuth login flow and local token storage for Google Tasks access
- [ ] Wire the new server into the Nanobot launcher and runtime configuration
- [ ] Add environment variable and configuration documentation
- [ ] Update deployed Nanobot workspace instructions so task requests can route to GTasks
- [ ] Add focused tests for the new integration points
- [ ] Run verification for the new code paths

## Intended Implementation Shape

- [x] Separate write-capable tool surface from the deterministic sensor pipeline
- [ ] Create a small Google Tasks client wrapper
- [ ] Expose task operations through a dedicated MCP server
- [ ] Support at least task creation and task listing
- [ ] Keep credentials and OAuth tokens outside committed source files

## Notes

- [x] The current repo policy and tool layout are sensor-first and read-only by default
- [x] Earlier discussion identified that a previous attempt leaned on a cron-style reminder path rather than a real Google Tasks integration
- [ ] Exact API dependency selection and OAuth token file path still need to be finalized during implementation
