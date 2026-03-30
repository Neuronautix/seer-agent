# Agent Policy

This project treats the deterministic sensor pipeline as the only source of truth.

Rules:
- The LLM may emit only these intents: `read_latest`, `summarize_window`, `get_threshold_status`, `request_export`, `propose_annotation`.
- The LLM must never generate canonical observation JSON-LD.
- The LLM must never write directly to logs, schemas, Pod resources, local storage, or external repositories.
- Nanobot live-environment queries may only use read-only tools that read validated local files.
- Nanobot must never read `/dev/tty*` devices or any direct serial source.
- The LLM must never bypass schema validation or supervisor checks.
- Any non-read-only action must be returned as a proposal object only.
- Proposal objects are advisory and require deterministic supervisor review before any execution.
- If a request falls outside the allowed intents or permissions, the correct behavior is refusal or a safe read-only alternative.

Enforcement:
- Allowed output format: `schemas/agent-action-v1.json`.
- Canonical sensor records may only come from the deterministic ingestion pipeline.
- Execution authority stays in Python control logic, not in the LLM.
- Allowed Nanobot tool inputs: validated local files such as `logs/latest-observation.json` and `logs/validated-observations.jsonl`.
- Supported live metrics are `temperature`, `humidity`, and `pressure`.