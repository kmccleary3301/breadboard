# Claims and Evidence Ledger (v1)

Purpose: keep public messaging tied to reproducible artifacts and scripts.

## Confidence Scale

- `H`: evidence is documented and reproducible now.
- `M`: partially covered; evidence exists but breadth is still expanding.
- `L`: planned or early; not ready for broad public claims.

## Current Ledger

| Claim | Confidence | Evidence / Source | Public Wording |
|---|---|---|---|
| Runs are replayable artifacts. | H | `docs/TUI_TIER1_GOLDENS_RUNBOOK.md`, `docs/log_reduce_tool.md`, run dirs under `logging/` | "Runs are replayable and diffable with artifact-backed workflows." |
| Contract-backed streaming surfaces exist (CLI bridge + schemas). | H | `docs/CLI_BRIDGE_PROTOCOL_VERSIONING.md`, `docs/contracts/` | "Streaming and tool events are contract-backed and versioned." |
| UI projection boundaries are explicit. | M | `docs/TUI_TODO_EVENT_CONTRACT.md`, `docs/TUI_THINKING_STREAMING_CONFIG.md` | "UI surfaces consume projection contracts; coverage is evolving." |
| Portability/conformance lanes exist and are being hardened. | M | `docs/conformance/`, `docs/TUI_TIER1_GOLDENS_RUNBOOK.md` | "Portability is validated with fixtures/goldens and published coverage." |
| External harness import/resume support exists. | M | converter scripts and replay docs in `docs/cli_phase_2/`, `misc/` | "Import/resume is supported for covered harness formats; matrix is published as coverage." |
| Provider plan adapters (strict full matrix) are complete. | L/M | provider plan docs under `docs/provider_plans/` | "Provider-plan support is rolling out with explicit per-provider coverage." |

## Red-Line Claims (Do Not Use)

- "Drop-in replacement for Claude Code / Codex / OpenCode."
- "Perfect parity."
- "Supports every provider and plan seamlessly."
- "Enterprise-ready security posture" without published security posture and controls.

## Release Checklist for New Claims

1. Add/refresh fixture or replay scenario.
2. Add script command that reproduces evidence.
3. Add docs link and report artifact path.
4. Update this ledger confidence and wording.
5. Ensure wording is conservative when confidence is `M` or `L`.
