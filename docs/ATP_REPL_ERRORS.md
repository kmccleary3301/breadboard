# ATP REPL Error Codes

This document defines machine-readable ATP REPL error codes surfaced by:

- `POST /atp/repl`
- `POST /atp/repl/batch`
- `POST /atp/v1/repl`
- `POST /atp/v1/repl/batch`
- `POST /ext/evolake/tools/atp_repl`

Response fields:

- `api_version` (string): versioned contract id (`atp.repl.v1`).
- `error_code` (string): stable machine-readable code.
- `error_detail` (object): structured context for diagnostics.
- `harness_diagnostic` (object|null): normalized diagnostic classification, severity,
  and operator action derived from `error_code`.

## Codes

| `error_code` | Trigger | `error_detail` keys |
| --- | --- | --- |
| `unsupported_mode` | Request mode is not command mode for Firecracker REPL | `mode` |
| `state_ref_not_found` | Requested `state_ref` is not present in state store | `state_ref` |
| `state_ref_incompatible` | `state_ref` metadata does not match requested header/toolchain/mathlib | `state_ref`, `header_hash` |
| `state_ref_tenant_mismatch` | `state_ref` tenant metadata does not match request tenant | `state_ref`, `tenant_id`, `blob_tenant_id` |
| `state_ref_corrupt` | `state_ref` payload/metadata is malformed or unreadable | `state_ref` |
| `invalid_timeout` | `limits.timeout_s <= 0` | `timeout_s` |
| `invalid_memory` | `limits.memory_mb <= 0` | `memory_mb` |
| `invalid_heartbeats` | `limits.max_heartbeats <= 0` | `max_heartbeats` |
| `limit_timeout_exceeded` | `limits.timeout_s` exceeds configured service cap | `timeout_s`, `max_timeout_s` |
| `limit_memory_exceeded` | `limits.memory_mb` exceeds configured service cap | `memory_mb`, `max_memory_mb` |
| `limit_heartbeats_exceeded` | `limits.max_heartbeats` exceeds configured service cap | `max_heartbeats`, `max_heartbeats_cap` |
| `protocol_mismatch` | Guest protocol handshake/shape mismatch | `stage`, transport-specific fields |
| `protocol_response_invalid` | Non-parseable or malformed protocol response payload | `stage`, transport-specific fields |
| `protocol_transport_error` | Transport-level REPL execution failure | `stage`, transport-specific fields |
| `repl_port_conflict` | REPL vsock port conflict (`Address already in use`) | `stage`, transport-specific fields |
| `protocol_batch_mismatch` | Batch route produced cardinality mismatch (`requests/results/metrics`) | `requests`, `results`, `metrics` |

## Stability Contract

- New codes may be added.
- Existing codes must remain stable and backward compatible.
- Existing key names in `error_detail` should not be renamed.

## Verification

Coverage tests:

- `tests/test_atp_repl_error_codes.py`
- `tests/test_atp_repl_batch.py`
- `tests/test_atp_harness_stateful_integration.py`
- `tests/test_atp_harness_diagnostics.py`
- `tests/test_atp_repl_tenant_metadata.py`
- `tests/test_firecracker_repl_limits.py`
- `tests/test_firecracker_protocol_errors.py`
- `tests/test_firecracker_state_ref_errors.py`
