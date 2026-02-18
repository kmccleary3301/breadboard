# BreadBoard Contract Surfaces

This document is the top-level map of contract boundaries that should remain stable as BreadBoard evolves.

## 1) Run Artifact Contract (Replay Surface)

Primary artifact:

- `logging/<run-id>/meta/events.jsonl`

Properties:

- append-only event rows,
- deterministic ordering semantics for replay/eval workflows,
- portable enough to support reduced transcript and replay analysis tooling.

Related docs:

- `docs/log_reduce_tool.md`
- `docs/PARITY_KERNEL_BOUNDARIES.md`

## 2) Live Streaming Contract (CLI Bridge / SSE)

Primary surface:

- CLI bridge HTTP + SSE protocol.

Core references:

- `docs/CLI_BRIDGE_PROTOCOL_VERSIONING.md`
- `docs/contracts/cli_bridge/openapi.json`
- `docs/contracts/cli_bridge/schemas/`

Validation/export:

```bash
python scripts/export_cli_bridge_contracts.py
```

## 3) Projection Contract (Operator UI Surface)

BreadBoard operator UIs consume projected envelopes and normalized payload fields rather than private engine internals.

Core references:

- `docs/TUI_TODO_EVENT_CONTRACT.md`
- `docs/TUI_THINKING_STREAMING_CONFIG.md`
- `docs/TUI_THINKING_STREAMING_TROUBLESHOOTING.md`

## 4) Portability + Parity Contract

Portability and parity are governed by explicit fixture/replay lanes and bounded kernel surfaces.

Core references:

- `docs/PARITY_KERNEL_BOUNDARIES.md`
- `scripts/run_parity_replays.py`
- `docs/conformance/`

## 5) Stability Bands

- **Stable**:
  - versioned CLI bridge schemas,
  - replay artifact structure relied on by existing tooling.
- **Evolving**:
  - projection payload enrichments for TUI quality-of-life,
  - new portability adapters and provider-specific normalizations.

All public claims for these surfaces must be tracked in:

- `docs/CLAIMS_EVIDENCE_LEDGER.md`

## 6) Change Policy (Practical)

Before changing any contract surface:

1. Update docs and schemas first.
2. Regenerate exported contracts where applicable.
3. Run smoke/replay validations.
4. Record claim deltas in the evidence ledger.
