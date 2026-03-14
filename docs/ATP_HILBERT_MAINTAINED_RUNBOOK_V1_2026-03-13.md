# ATP Maintained-Hilbert Runbook v1 — 2026-03-13

## Purpose

This runbook defines the maintained-Hilbert ATP comparison lane used for BreadBoard-versus-Hilbert tranches after Packs B through J.

It standardizes:

- pack construction
- BreadBoard execution
- maintained-Hilbert execution
- result conversion
- validation
- paired report generation
- rollup regeneration

## Preconditions

- repo root: current BreadBoard checkout root
- maintained comparator repo: local checkout of `other_harness_refs/ml-hilbert`
- verifier is reachable
- `OPENROUTER_API_KEY` is available in the BreadBoard repo `.env`

Current comparison identities:

- BreadBoard candidate system: `bb_hilbert_like`
- Hilbert baseline system: `hilbert_roselab`

## Canonical build inputs

Pack builder:

- `scripts/build_hilbert_comparison_packs_v2.py`

Policy and baseline locks:

- `docs/contracts/benchmarks/ATP_HILBERT_TRANCHE_SELECTION_POLICY_V1.md`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/invalid_extract_ledger_v1.json`

## Step 1 — Build the tranche

Example for a new pack:

```bash
cd <repo_root>
python scripts/build_hilbert_comparison_packs_v2.py --pack pack_k_your_new_pack_here
```

Expected outputs under:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/`

Minimum required artifacts:

- `pack_metadata.json`
- `hilbert_dataset.jsonl`
- `cross_system_manifest.json`
- `bb_task_inputs.json`

## Step 2 — Run BreadBoard

Direct formal runner:

```bash
cd <repo_root>
python scripts/run_bb_formal_pack_v1.py \
  --manifest artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_manifest.json \
  --task-inputs artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_task_inputs.json \
  --out artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_hilbert_like_results_v1.jsonl \
  --summary-out artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_hilbert_like_slice_summary_v1.json \
  --proof-output-dir artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_hilbert_like_proofs_v1 \
  --raw-output-dir artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_hilbert_like_raw_v1 \
  --verifier-url http://127.0.0.1:18001/ \
  --config agent_configs/atp_hilbert_like_gpt54_v2.yaml \
  --max-iterations 8
```

What to expect:

- result rows in `bb_hilbert_like_results_v*.jsonl`
- summary in `bb_hilbert_like_slice_summary_v*.json`
- pack-level usage ledger in `bb_hilbert_like_usage_ledger_v*.json`

## Step 3 — Run maintained Hilbert

Use bounded settings and sequential processing.

Example:

```bash
cd <hilbert_repo_root>
set -a && source <repo_root>/.env && set +a
OPENAI_API_KEY="$OPENROUTER_API_KEY" python -m src.run \
  data.name=<pack_id> \
  data.file_path=<repo_root>/artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/hilbert_dataset.jsonl \
  results_dir=<repo_root>/artifacts/benchmarks/hilbert_runs/<hilbert_run_id>/results \
  experiment.proof_save_dir=<repo_root>/artifacts/benchmarks/hilbert_runs/<hilbert_run_id>/proofs \
  experiment.prover_llm.base_url=https://openrouter.ai/api/v1 \
  experiment.prover_llm.llm_name=openai/gpt-5.4 \
  experiment.informal_llm.base_url=https://openrouter.ai/api/v1 \
  experiment.informal_llm.model_name=openai/gpt-5.4 \
  experiment.sequential_processing=true \
  experiment.max_concurrent_problems=1 \
  experiment.max_concurrent_requests=4 \
  experiment.max_depth=2 \
  experiment.subgoal_decomp_attempts=2 \
  experiment.formal_proof_attempts=2 \
  experiment.main_theorem_error_corrections=3 \
  experiment.subgoal_error_corrections=2 \
  experiment.proof_sketch_corrections=3 \
  experiment.max_prover_llm_calls=12 \
  experiment.max_reasoner_llm_calls=8 \
  verifier_base_url=http://127.0.0.1:18001/
```

## Step 4 — Convert maintained-Hilbert results

```bash
cd <repo_root>
python scripts/convert_hilbert_results_to_cross_system_v2.py \
  --manifest artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_manifest.json \
  --hilbert-result-json artifacts/benchmarks/hilbert_runs/<hilbert_run_id>/results/async_hilbert/<result_file>.json \
  --out artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/hilbert_roselab_results_v1.jsonl \
  --summary-out artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/hilbert_roselab_summary_v1.json
```

## Step 5 — Validate and build paired report

```bash
cd <repo_root>
python scripts/validate_cross_system_run_v1.py \
  --manifest artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_manifest.json \
  --results artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/hilbert_roselab_results_v1.jsonl \
  --results artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_hilbert_like_results_v1.jsonl \
  --json-out artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_validation_report_v1.json

python scripts/build_pilot_comparison_report_v1.py \
  --manifest artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_manifest.json \
  --results artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/hilbert_roselab_results_v1.jsonl \
  --results artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/bb_hilbert_like_results_v1.jsonl \
  --baseline-system hilbert_roselab \
  --candidate-system bb_hilbert_like \
  --out-json artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_pilot_report_v1.json \
  --out-md artifacts/benchmarks/hilbert_comparison_packs_v2/<pack_id>/cross_system_pilot_report_v1.md
```

## Step 6 — Status note

For every new canonical or supporting tranche, add one note in `docs/` that records:

- tranche scope
- canonical report path
- canonical validation path
- current result
- spend note
- whether the result is baseline-only, focused-repaired, split-rollup, or stress

## Step 7 — Rollup regeneration

After canonical promotion or support-pack changes:

```bash
cd <repo_root>
python scripts/build_atp_hilbert_rollup_v1.py
python scripts/build_atp_invalid_extract_ledger_v1.py
python scripts/build_atp_hilbert_no_repair_slice_v1.py
python scripts/build_atp_hilbert_repair_intensity_v1.py
```

This refreshes:

- canonical baseline index
- arm audit
- BreadBoard spend backfill
- scoreboard
- invalid extract ledger
- no-repair slice
- repair-intensity artifact

## Interpretation policy

Do not make aggregate claims from:

- stale report versions
- invalid extracted theorems
- partial normalized reruns
- non-canonical full-pack reruns that regress solved tasks

Do make aggregate claims from:

- the canonical baseline index
- the program scoreboard
- the arm audit
- the no-repair slice
- the repair-intensity artifact

## Completion hygiene

When tranche work changes code or canonical artifacts:

```bash
pytest -q tests/test_build_atp_hilbert_arm_audit_v1.py \
  tests/test_build_atp_hilbert_canonical_baselines_v1.py \
  tests/test_build_atp_hilbert_no_repair_slice_v1.py \
  tests/test_build_atp_hilbert_repair_intensity_v1.py \
  tests/test_build_atp_hilbert_rollup_v1.py \
  tests/test_build_atp_hilbert_scoreboard_v1.py \
  tests/test_backfill_atp_hilbert_bb_spend_v1.py \
  tests/test_build_atp_invalid_extract_ledger_v1.py \
  tests/test_run_bb_formal_pack_v1.py

python -m py_compile \
  scripts/build_atp_hilbert_arm_audit_v1.py \
  scripts/build_atp_hilbert_canonical_baselines_v1.py \
  scripts/build_atp_hilbert_no_repair_slice_v1.py \
  scripts/build_atp_hilbert_repair_intensity_v1.py \
  scripts/build_atp_hilbert_rollup_v1.py \
  scripts/build_atp_hilbert_scoreboard_v1.py \
  scripts/backfill_atp_hilbert_bb_spend_v1.py \
  scripts/build_atp_invalid_extract_ledger_v1.py \
  scripts/run_bb_formal_pack_v1.py
```

Then finish with:

```bash
bd sync
git pull --rebase
git push
git status
```
