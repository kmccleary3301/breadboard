# BreadBoard conformance suite

This directory hosts the executable conformance assets for BreadBoard's core contract families. CI runs the full Wave A bundle on every push. This document describes the structure, how to run things locally, and how to triage failures.

---

## Directory layout

- `schemas/` — canonical draft schema set (`bb.*.v1.schema.json`)
- `CONFORMANCE_TEST_MATRIX_V1.md` — current CT-* matrix (source of truth for gate tracking)
- `CONFORMANCE_TEST_MATRIX_V1.csv` — machine-readable matrix for dashboards
- `tests/fixtures/conformance_v1/fixtures/` — valid/invalid fixture corpus mapped to schema IDs
- `*_runtime_evidence/` — checked-in non-fixture replay evidence baselines

---

## Running the suite locally

### Full Wave A bundle (recommended)

```bash
scripts/run_wave_a_conformance_bundle.sh artifacts/conformance
```

This runs: schema gate + replay determinism gate + CT scenario runner + matrix sync.

### Individual runners

```bash
# Conformance matrix (schema + fixture gate)
python scripts/run_conformance_matrix.py
python scripts/run_conformance_matrix.py --json-out artifacts/conformance/conformance_report_v1.json

# CT scenario runner (strict mode)
python scripts/run_ct_scenarios.py \
  --fail-on-unimplemented-all \
  --json-out artifacts/conformance/ct_scenarios_result_v1.json \
  --rows-out artifacts/conformance/ct_scenarios_rows_v1.json
```

### E4 target freeze check

```bash
make e4-target-manifest
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
```

Policy: [E4_TARGET_VERSIONING.md](E4_TARGET_VERSIONING.md)

---

## Semantic checker scripts

These are the helpers that selected CT-* rows call into:

| Script | What it checks |
|--------|----------------|
| `scripts/check_projection_semantics.py` | Cursor monotonicity, immutable prefix, heartbeat bounds, reconnect/coalescing/gap semantics |
| `scripts/check_tool_transition_semantics.py` | Tool lifecycle allowed-edge validation |
| `scripts/check_tool_policy_semantics.py` | One-bash-per-turn, disabled-tool mask, runtime invalid-transition semantics |
| `scripts/check_approval_semantics.py` | Approval coherence, webfetch deny, network allowlist, lifecycle audit-stream invariants |
| `scripts/check_provider_semantics.py` | Provider tie-break, auth precedence, retry-after normalization, replay runtime delegation |
| `scripts/check_projection_overlay_semantics.py` | Revision-keyed projection overlay semantics |
| `scripts/check_rollback_semantics.py` | Dropped-range rewind semantics |
| `scripts/check_plan_envelope_semantics.py` | Deterministic plan decision-envelope lifecycle |
| `scripts/check_session_semantics.py` | Resume/fork prefix, pending permission partition, task-scope isolation, conflict diagnostics |
| `scripts/check_extension_semantics.py` | Extension middleware phase/priority ordering and dependency ordering invariants |
| `scripts/check_plugin_discovery_semantics.py` | Plugin discovery and snapshot determinism from configured search paths |
| `scripts/check_replay_placeholder_semantics.py` | Session placeholder resolution from deterministic todo snapshots |
| `scripts/check_surface_manifest_semantics.py` | Surface-manifest determinism propagation from MCP snapshots |
| `scripts/check_longrun_semantics.py` | Long-run recursion budget/profile/rollback semantics |
| `scripts/check_protocol_semantics.py` | Protocol contract export, envelope variant, payload-sample, and runtime-translation semantics |
| `scripts/check_projection_contract_parity.mjs` | SDK vs sidebar projection contract parity |
| `scripts/check_projection_contract_parity_opentui.mjs` | SDK vs OpenTUI slab adapter parity |
| `bb_webapp/scripts/check_projection_contract_parity.ts` | SDK vs webapp projection contract parity |
| `tui_skeleton/scripts/check_projection_contract_parity.ts` | SDK vs TUI transcript-normalization parity |
| `vscode_sidebar/scripts/check_projection_reducer_parity.mjs` | SDK semantics vs VSCode transcript-reducer entry projection |
| `scripts/check_projection_surface_parity_bundle.py` | Aggregate hard gate across SDK/OpenTUI/webapp/TUI/VSCode projection parity reports |
| `scripts/audit_longrun_parity_disabled.py` | Parity/E4 profile guard: long-running mode disabled |
| `scripts/check_reliability_lanes.py` | `ALWAYS_PASSES`/`USUALLY_PASSES` lane policy checks |
| `scripts/build_phase5_reliability_flake_report.py` | Trend report builder over reliability replay history |
| `scripts/check_phase5_reliability_flake_budget.py` | Hard gate for trend classifications/budgets |
| `scripts/check_conformance_matrix_semantics.py` | Schema/fixture conformance report semantic coherence |
| `scripts/check_sync_mapping_semantics.py` | Exact `test_id` mapping semantics for matrix sync; no family fallback |
| `scripts/check_conformance_sync_freshness.py` | Stale checked-in synced matrix detection |
| `scripts/promote_replay_evidence_baselines.py` | Strict promotion utility for refreshing checked-in non-fixture replay evidence baselines |

---

## Stability baselines

Three drift-sensitive baselines are enforced in CI:

- `docs/conformance/request_body_hashes_baseline_v1.json` via `scripts/check_request_body_hashes.py`
- `docs/conformance/tool_schema_hashes_baseline_v1.json` via `scripts/check_tool_schema_hashes.py`
- `docs/conformance/event_envelope_snapshots_baseline_v1.json` via `scripts/check_event_envelope_snapshots.py`

---

## Replay determinism gate

The gate accepts repeated `--glob` inputs. Wave A currently merges fixture-backed reports, checked-in non-fixture replay evidence baselines, and local conformance-QC replay reports, with:

- minimum total evidence floor: 8 reports (`--min-files 8`)
- minimum real (non-fixture) evidence floor: 6 reports (`--min-non-fixture-files 6`)
- minimum turn-depth per non-fixture report: 15 turns (`--min-non-fixture-turns 15`)

---

## Artifact contract

Wave A emits these required artifacts under `artifacts/conformance/`:

1. `conformance_matrix_report_v1.json`
2. `replay_determinism_gate_report_v1.json`
3. `ct_scenarios_result_v1.json`
4. `ct_scenarios_rows_v1.json`
5. `reliability_lane_report_v1.json`
6. `reliability_flake_report_v1.json`
7. `reliability_flake_report_v1.md`
8. `reliability_flake_budget_report_v1.json`
9. `reliability_flake_budget_report_v1.md`
10. `longrun_parity_audit_v1.json`
11. `projection_contract_parity_report_v1.json`
12. `projection_contract_parity_opentui_report_v1.json`
13. `projection_contract_parity_webapp_report_v1.json`
14. `projection_contract_parity_tui_report_v1.json`
15. `projection_reducer_parity_vscode_report_v1.json`
16. `projection_surface_parity_bundle_v1.json`
17. `conformance_family_summary_v1.json`
18. `conformance_matrix_sync_summary_v1.json`
19. `CONFORMANCE_TEST_MATRIX_V1.synced.csv`
20. `node_gate/*.json`
21. `junit/` directory (may be empty when no row emits JUnit XML)

Validate artifacts:

```bash
python scripts/validate_conformance_artifacts.py \
  --artifact-dir artifacts/conformance \
  --node-gate-dir artifacts/conformance/node_gate \
  --junit-dir artifacts/conformance/junit
```

Custom output roots:

```bash
tmpdir="$(mktemp -d)"
scripts/run_wave_a_conformance_bundle.sh "$tmpdir"
python scripts/validate_conformance_artifacts.py \
  --artifact-dir "$tmpdir" \
  --node-gate-dir "$tmpdir/node_gate" \
  --junit-dir "$tmpdir/junit"
```

Validator invariants:

- All CT rows must pass for bundle validation.
- `conformance_matrix_sync_summary_v1.json` counters must be internally consistent and match `ct_scenarios_rows_v1.json`.
- `CONFORMANCE_TEST_MATRIX_V1.synced.csv` status counts must match sync summary counts.
- All manifest-declared `artifacts/conformance/node_gate/*.json` outputs must exist.

---

## Triage order on failure

1. `replay_determinism_gate_report_v1.json` — check `required_files_met`, `failure_count`, and failure signatures.
2. `ct_scenarios_result_v1.json` — identify failing CT-* rows, then inspect `ct_scenarios_rows_v1.json`.
3. `node_gate/*.json` — inspect per-row `exit_code`, `failures`, `errors`, and `stderr_excerpt`.
4. `conformance_matrix_sync_summary_v1.json` — verify row mapping and aggregate pass/fail counts.
5. `CONFORMANCE_TEST_MATRIX_V1.synced.csv` — confirm row-level status and notes.

---

## Extending coverage

To add a new CT row: [ADDING_CT_ROW_GUIDE.md](ADDING_CT_ROW_GUIDE.md)

To add a new semantic checker: [ADDING_SEMANTIC_CHECKER_GUIDE.md](ADDING_SEMANTIC_CHECKER_GUIDE.md)
