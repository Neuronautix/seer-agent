# Solid Pod Implementation

This repository is not a Pod today.

Current state:

- Serial ingest runs as a host `systemd` service.
- The read-only API runs as a host `systemd` service.
- Nanobot and WhatsApp are optional host processes or services.
- Persistence uses local files under `logs/`.

The goal of a solid Pod deployment is to package these pieces into a repeatable, isolated, boot-stable unit with explicit boundaries and minimal operational drift.

## Target Outcome

Build a deployment that behaves like a solid Pod on a Raspberry Pi or small Linux host:

- deterministic sensor ingest stays the source of truth
- read-only API remains the only live read surface
- chat and WhatsApp remain optional overlays
- startup is automatic and reproducible
- data and runtime state live in explicit mounted paths
- updates are controlled and reversible

## Scope

The Pod implementation should cover:

- packaging and process supervision
- persistent storage layout
- restart behavior after power loss or reboot
- network exposure and boot ordering
- environment and secret injection
- optional WhatsApp sidecar or companion service model
- health checks and failure visibility

## Proposed Architecture

Minimum packaging model:

1. `sensor-ingest` unit or container
   Reads serial data and writes validated observations.
2. `sensor-api` unit or container
   Serves read-only HTTP endpoints from validated files.
3. `nanobot-gateway` optional unit or container
   Connects to the read-only tool layer and exposes the messaging interface.
4. `whatsapp-bridge` optional companion process
   Runs only when WhatsApp support is needed.

Shared persistent paths:

- `logs/`
- Nanobot runtime state directory
- WhatsApp auth/runtime state directory

## Implementation Plan

### Phase 1: Host-hardening

- Keep `systemd` as the baseline production path.
- Finalize the boot profile: ingest and API always on, Nanobot manual by default.
- Add explicit health checks for ingest freshness and API readiness.
- Add a small operational status command that reports data freshness, API status, and Nanobot status.

### Phase 2: Pod packaging design

- Choose packaging target: Podman pod, Docker Compose, or another lightweight multi-service runtime.
- Define one persistent volume layout for validated logs and runtime data.
- Define one environment file strategy for secrets and deployment-specific settings.
- Define how `/dev/ttyACM0` is passed safely to ingest only.

### Phase 3: Service decomposition

- Put ingest in its own container or unit with serial-device access.
- Put API in its own container or unit with read-only access to validated files.
- Keep Nanobot isolated from raw serial and ingestion code.
- Keep WhatsApp runtime state separate from source-controlled files.

### Phase 4: Observability and alarms

- Add freshness alarms when no new validated observations arrive within a configured interval.
- Add health endpoints or status files for each process.
- Add structured startup diagnostics and failure reasons.

### Phase 5: Update and recovery workflow

- Define a one-command deploy or upgrade path.
- Define rollback steps.
- Define backup and restore steps for logs and messaging runtime state.

## Required Design Decisions

- Podman pod versus Docker Compose
- whether Nanobot is always deployed or remains optional
- where WhatsApp auth state is stored
- whether the API is LAN-only or reverse-proxied
- how secrets are injected on the Pi

## Acceptance Criteria

A deployment counts as a solid Pod when:

- the Pi can reboot unattended and ingest plus API come back automatically
- validated observations continue to update after restart without manual intervention
- Nanobot can be started or stopped independently without breaking ingest or API
- raw serial access stays limited to ingest only
- all persistent data paths are explicit and documented
- logs and runtime state are not mixed with source-controlled files
- the deploy and recovery workflow is documented and tested

## Repository TODO Link

Track this work under the roadmap item:

- `Define and implement a solid Pod-style deployment package`