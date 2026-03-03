# Contract surfaces

This is the top-level map of the contract boundaries that should stay stable as BreadBoard evolves. Changes to surfaces listed here follow a documented change policy, not ad-hoc decisions.

---

## 1. Run artifact contract (replay surface)

Primary artifact:

```
logging/<run-id>/meta/events.jsonl
```

Properties:

- append-only event rows
- deterministic ordering semantics for replay and eval workflows
- portable enough to drive log reduction and replay analysis in `scripts/`

Related docs:

- [`PARITY_KERNEL_BOUNDARIES.md`](PARITY_KERNEL_BOUNDARIES.md)

---

## 2. Live streaming contract (CLI bridge / SSE)

Primary surface: the CLI bridge HTTP + SSE protocol.

Core references:

- [`CLI_BRIDGE_PROTOCOL_VERSIONING.md`](CLI_BRIDGE_PROTOCOL_VERSIONING.md)
- [`contracts/cli_bridge/openapi.json`](contracts/cli_bridge/openapi.json)
- [`contracts/cli_bridge/schemas/`](contracts/cli_bridge/schemas/)

Export current contract:

```bash
python scripts/export_cli_bridge_contracts.py
```

---

## 3. Projection contract (operator UI surface)

BreadBoard operator UIs consume projected envelopes and normalized payload fields. They do not have access to engine internals.

Core references:

- [`TUI_TODO_EVENT_CONTRACT.md`](TUI_TODO_EVENT_CONTRACT.md)
- [`TUI_THINKING_STREAMING_CONFIG.md`](TUI_THINKING_STREAMING_CONFIG.md)
- [`TUI_THINKING_STREAMING_TROUBLESHOOTING.md`](TUI_THINKING_STREAMING_TROUBLESHOOTING.md)

---

## 4. Portability and parity contract

Portability and parity are governed by explicit fixture/replay lanes and bounded kernel surfaces.

Core references:

- [`PARITY_KERNEL_BOUNDARIES.md`](PARITY_KERNEL_BOUNDARIES.md)
- `scripts/run_parity_replays.py`
- [`conformance/`](conformance/README.md)

---

## 5. Stability bands

**Stable** — breaking changes require explicit versioning and a recorded ADR:

- versioned CLI bridge schemas
- replay artifact structure relied on by existing tooling

**Evolving** — changes are allowed without a full ADR, but must be reflected in docs:

- projection payload enrichments for TUI quality-of-life improvements
- new portability adapters and provider-specific normalizations

All public claims for these surfaces must be tracked in:

- [`CLAIMS_EVIDENCE_LEDGER.md`](CLAIMS_EVIDENCE_LEDGER.md)

---

## 6. Change policy

Before changing any contract surface:

1. Update docs and schemas first.
2. Regenerate exported contracts where applicable (`python scripts/export_cli_bridge_contracts.py`).
3. Run smoke and replay validations.
4. Record claim deltas in the evidence ledger.

For breaking changes, also:

- Complete the [`contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md`](contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md).
- File or reference an ADR in [`contracts/policies/adr/`](contracts/policies/adr/).
