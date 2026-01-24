# BreadBoard CLI Bridge — Protocol Versioning

The BreadBoard engine exposes a **CLI bridge API** (FastAPI) with a streaming SSE event protocol. This document defines how we version and evolve that protocol without breaking clients (TUI/SDKs).

## Where the version lives

- `agentic_coder_prototype/api/cli_bridge/events.py` defines `PROTOCOL_VERSION`.
- Every streamed event includes `protocol_version` (see `SessionEvent.asdict()`).
- `GET /health` also reports `protocol_version`.

## Versioning rules

We use a **semantic-like** protocol version string: `MAJOR.MINOR`.

### Bump **MINOR** (backwards-compatible)

Allowed changes that should **not** break existing clients:

- Add a new event type.
- Add new fields to an event payload.
- Add new fields to existing REST responses.
- Add new endpoints.
- Expand allowed values where existing values still work.

### Bump **MAJOR** (breaking)

Changes that can break existing clients:

- Remove or rename event types.
- Change the meaning of an existing field in a way that breaks old assumptions.
- Remove fields from payloads or responses.
- Change field types (e.g. `string` → `object`).
- Change ordering/semantics of tool result insertion in a way that alters client logic.

If a breaking change is required, prefer *adding* new fields/types first, deprecating old ones, and only then bumping MAJOR when removing support.

## Client expectations (TUI/SDK)

Clients should:

- Ignore unknown top-level event fields and unknown payload fields.
- Treat `protocol_version` as informative unless explicitly incompatible.
- Avoid hard-failing on new event types; instead, render them in a generic “unknown event” row when possible.

## Compatibility testing

When changing the protocol:

1. Update `PROTOCOL_VERSION` if needed.
2. Update `docs/contracts/cli_bridge/openapi.json` (and ensure `scripts/export_cli_bridge_contracts.py` produces no diff).
3. Add/extend targeted tests covering the new protocol surface.

