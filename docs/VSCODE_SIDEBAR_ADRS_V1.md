# VSCode Sidebar ADRs (V1)

Last updated: `2026-02-21`

This document freezes core architecture decisions for the BreadBoard VSCode/Cursor sidebar interface.

## Scope

These ADRs cover:

1. Host and webview split.
2. Transport strategy.
3. Contract boundaries.
4. Session resume behavior.
5. Security baseline.

They align with:

- `docs/CONTRACT_SURFACES.md`
- `docs/CLI_BRIDGE_PROTOCOL_VERSIONING.md`
- `docs/EXTENSION_MIDDLEWARE_SPINE.md`
- `docs/contracts/cli_bridge/openapi.json`

## ADR-001: Client Architecture

Decision:

- Use a hybrid architecture:
  - Extension host (Node) is the stateful controller.
  - Webview (React/TS) is the renderer and interaction surface.

Why:

- The host is the right place for tokens, SSE reconnection, and durable session bookkeeping.
- The webview should remain a pure projection of normalized state/events.
- This mirrors existing BreadBoard controller->normalized-events->render patterns used in current TUI work.

Consequences:

- No direct engine HTTP/SSE access from the webview.
- Host defines strict typed RPC between host and webview.

## ADR-002: Engine Transport

Decision:

- Use existing CLI bridge HTTP + SSE contracts.

Why:

- Contract already versioned and exported.
- Resume semantics (`Last-Event-ID`) already defined.
- Avoid unnecessary websocket complexity in V1.

Consequences:

- Sidebar must preserve event order from server.
- Sidebar must handle replay window limits and reconnect behavior.

## ADR-003: Contract Boundary

Decision:

- Engine event stream is canonical.
- UI projection is an adapter layer, not a new engine protocol.

Why:

- Prevent UI-specific drift from parity and replay surfaces.
- Keep E4 and contract guarantees stable across clients.

Consequences:

- Unknown future fields/events are tolerated in client parsing.
- IDE-only behaviors must be explicit capability/profile-gated.

## ADR-004: Session Resume and Gap Handling

Decision:

- Track `lastEventId` per session in host storage.
- Reconnect with `Last-Event-ID`.
- If replay window is exceeded, flag a continuity gap and fall back to artifact rebuild flow.

Why:

- Engine replay buffer is bounded; gaps are possible after long disconnects.
- Explicit gap handling avoids silent state divergence.

Consequences:

- Host connection state includes `resume_mode` and `gap_detected`.
- UX must expose transparent recovery state to operators.

## ADR-005: Security Baseline

Decision:

- Treat webview as untrusted rendering surface.
- Keep credentials in VSCode `SecretStorage` only.
- Enforce workspace trust gating before write/exec flows.

Why:

- Webviews have XSS/message spoofing risk if not hardened.
- Agent tools can execute destructive operations; trust state must be explicit.

Consequences:

- No token values in webview payloads.
- Strict runtime validation for every host<->webview RPC payload.
- Default deny posture for untrusted workspace write/exec actions.

## ADR-006: Cursor Compatibility Policy

Decision:

- Do not depend on `vscode.lm` or other APIs that may not be implemented in Cursor.

Why:

- BreadBoard already has its own engine transport and model routing layer.
- This keeps VSCode/Cursor compatibility higher and simpler.

Consequences:

- Sidebar uses BreadBoard engine contracts uniformly across editors.
- Add explicit Cursor smoke checks in quality lanes.

## Change Control

Any ADR change must include:

1. Reason for change.
2. Contract impact assessment.
3. Required test/CI gate updates.
4. Documentation updates in both `docs/` and relevant plan docs.

