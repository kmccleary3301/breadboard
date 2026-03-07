# ATP Vsock Protocol Policy

This document defines the canonical ATP vsock wire contract and where legacy
fallback behavior is allowed.

## Canonical Envelope (required)
- Protocol version source of truth: `breadboard/vsock_protocol.py` constant
  `REPL_VSOCK_PROTOCOL_VERSION`.
- Handshake request shape:
  - `{"type":"hello","version":<int>,"capabilities":{...}}`
- Handshake acknowledgement shape:
  - `{"type":"hello_ack","version":<int>,"capabilities":{...}}`
- REPL request shape:
  - `{"type":"repl","version":<int>,"payload":{"command":...,"timeout":...}}`
- REPL response shape:
  - `{"type":"repl_response","version":<int>,"response":{...}}`
  - or structured unsupported response with `error.code == "repl_unsupported"`.

Canonical fixtures are maintained under:
- `breadboard_ext/atp/protocol/vsock_fixtures/*`
- mirrored in `tests/fixtures/vsock_protocol/*`

## Legacy Fallback Policy
- Legacy wire payload `{"cmd":"..."}` exists only for compatibility with older
  REPL guests that do not support the envelope protocol.
- Legacy fallback is allowed only in ATP/firecracker execution paths:
  - `breadboard/sandbox_firecracker.py`
  - `breadboard/lean_repl/firecracker_adapter.py`
  - `breadboard/lean_repl/firecracker_service.py`
  - helper declaration in `breadboard/vsock_protocol.py`
- Non-ATP kernel paths and CLI bridge request/response contracts must not
  introduce or depend on `{"cmd":"..."}`.

## Compatibility Rules
- Envelope protocol remains the default and preferred path.
- Legacy fallback can be attempted only after a detected unsupported envelope
  response (`repl_unsupported` or equivalent).
- Legacy fallback must not be promoted to general request-shaping behavior.

## Machine Error Codes (protocol layer)
- `protocol_mismatch`: guest/host protocol shape mismatch (for example legacy-only
  guest replying `repl_unsupported` to envelope mode).
- `protocol_response_invalid`: malformed or non-parseable protocol response.
- `protocol_transport_error`: transport-level REPL execution failure not otherwise
  classified.
- `repl_port_conflict`: host-side REPL vsock port conflict (`EADDRINUSE` / address in use).
- `protocol_batch_mismatch`: API-level request/result/metrics cardinality mismatch in
  ATP batch route.

## Required Tests
- `tests/test_vsock_protocol.py`
- `tests/test_vsock_protocol_fallback.py`
- `tests/test_vsock_fixture_parity.py`
- `tests/test_vsock_envelope_policy.py`
- `tests/test_vsock_legacy_scope.py`

## Change Control
- Any envelope or fallback contract change requires:
  1. fixture updates
  2. test updates
  3. protocol change note via `docs/PROTOCOL_CHANGE_TEMPLATE.md`
