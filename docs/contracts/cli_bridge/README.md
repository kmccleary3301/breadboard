# CLI Bridge API Contracts

This folder captures the **frozen API contract** for the Breadboard CLI bridge.

Contents:
- `openapi.json` — OpenAPI spec for REST endpoints.
- `schemas/session_event_envelope.schema.json` — SSE event envelope schema.
- `schemas/session_event_payload_*.schema.json` — Selected payload schemas for high-value event types (tools, permissions, C-Trees).
- `schemas/session_create_request.schema.json` — POST `/sessions` request schema.
- `schemas/session_input_request.schema.json` — POST `/sessions/{id}/input` request schema.
- `schemas/session_command_request.schema.json` — POST `/sessions/{id}/command` request schema.

Regenerate with:

```bash
python scripts/export_cli_bridge_contracts.py
```

Note: Event payload **shape is intentionally open**. Only the envelope is frozen in this contract.
The payload schemas included here are **best-effort**: they define required keys for parity-critical UX (tools + permissions),
while still allowing additional provider-specific fields.
