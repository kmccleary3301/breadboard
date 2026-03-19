# DARWIN Phase-2 Tranche-1 Status

Date: 2026-03-18
Status: active implementation tranche
Scope: >40% tranche-1 push

## Current landed tranche-1 surfaces

The following shadow artifacts now emit on the tranche-1 proving lanes:

- `effective_config`
- `execution_plan`
- `effective_policy`
- `evaluator_pack`

Current proving-lane coverage:

- `lane.harness`
- `lane.repo_swe`

Current non-proving lanes remain unchanged for these additive artifacts:

- `lane.atp`
- `lane.systems`
- `lane.scheduling`
- `lane.research`

## Validation state

Validated on 2026-03-18:

- `pytest -q tests/test_darwin_contract_pack_v0.py tests/test_run_darwin_t1_live_baselines_v1.py`
- `python scripts/validate_darwin_contract_pack_v0.py --json`
- `python scripts/run_darwin_t1_live_baselines_v1.py --json`

Observed live-baseline shadow coverage:

- `lane.harness` emits `effective_config`, `execution_plan`, `effective_policy`, `evaluator_pack`
- `lane.repo_swe` emits `effective_config`, `execution_plan`, `effective_policy`, `evaluator_pack`
- other lanes emit no new shadow policy/evaluator artifacts yet

## Tranche read

What is now done:

- WP-01 `effective_config`
- WP-02 `execution_plan`
- WP-03 `effective_policy`
- shadow-only `EvaluatorPack` emission on `lane.harness`
- shadow-only `EvaluatorPack` emission on `lane.repo_swe`

What is not done yet:

- claim-bearing consumption of `EvaluatorPack`
- later dual-write consideration, if justified
- eventual tranche-end go/no-go on any runtime dependency change

## EvolutionLedger status

The tranche-1 `EvolutionLedger` layer now exists as a reconstruction-first DARWIN surface.

Current reconstructed outputs:

- typed `component_ref`
- typed `decision_record`
- `EvolutionLedger` container artifact
- representative Phase-1 case reconstruction for:
  - scheduling promotion history
  - harness → research transfer
  - repo_swe search/deprecation review

Current truth boundary:

- runtime truth remains outside the ledger
- archive snapshot remains derived-only
- promotion / transfer runtime artifacts remain their existing Phase-1 outputs
- ledger is reconstruction-first and not runtime-consumed

## Next move

The next correct tranche-1 move is:

- continue additive-only work under the tranche-1 hard-gate review
- keep runtime expansion blocked
- only after later review, consider whether any ledger dual-write is justified

Current next-tranche decision:

- `docs/darwin_phase2_next_tranche_decision_2026-03-18.md`

## Secondary audit coverage

Secondary audit-only packaging now exists for:

- `lane.scheduling`
- `lane.atp`

These artifacts confirm that the current `PolicyPack` / `EvaluatorPack` shapes extend beyond the two proving lanes without changing runtime behavior.
