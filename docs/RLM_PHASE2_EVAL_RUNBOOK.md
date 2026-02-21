# RLM Phase-2 Evaluation Runbook

This runbook defines the operational path for running RLM Phase-2 evaluation safely.

## 1) Preconditions

1. RLM tooling/tests green:
- `pytest -q tests/test_rlm_*.py tests/test_rlm_batch_load.py tests/test_validate_rlm_artifacts.py tests/test_conductor_loop_rlm_summary.py`

2. Parity safety green:
- `python scripts/audit_longrun_parity_disabled.py --repo-root .`

3. Environment keys loaded for live lanes (`.env`).

## 2) Offline Tier-0 (control-plane validation)

Run:
```bash
python scripts/run_rlm_phase2_tier0.py \
  --seeds 3 \
  --model-route mock/no_tools \
  --workspace-root ../tmp/rlm_phase2_tier0_s3 \
  --out-json ../docs_tmp/RLMs/PHASE_2_P5_RESULTS_RAW_V1.json \
  --out-markdown ../docs_tmp/RLMs/PHASE_2_P5_RESULTS_TABLE_V1.md \
  --out-memo ../docs_tmp/RLMs/PHASE_2_P5_GO_NO_GO_MEMO_V1.md
```

Evaluate:
```bash
python scripts/evaluate_rlm_phase2_p5.py \
  --results-json ../docs_tmp/RLMs/PHASE_2_P5_RESULTS_RAW_V1.json \
  --report-json ../docs_tmp/RLMs/PHASE_2_P5_EVAL_REPORT_V1.json
```

## 3) Live probe (capped)

Use both spend and token caps:
```bash
python scripts/run_rlm_phase2_tier0.py \
  --seeds 1 \
  --model-route openai/gpt-5.2 \
  --arm A0_BASELINE --arm A1_RLM_SYNC --arm A2_RLM_BATCH --arm A3_HYBRID_LONGRUN \
  --subcall-timeout-seconds 30 \
  --batch-timeout-seconds 30 \
  --spend-cap-usd 10 \
  --token-cap 30000 \
  --workspace-root ../tmp/rlm_phase2_live_tier1_s1 \
  --out-json ../docs_tmp/RLMs/PHASE_2_P5_LIVE_TIER1_RAW_V1.json \
  --out-markdown ../docs_tmp/RLMs/PHASE_2_P5_LIVE_TIER1_TABLE_V1.md \
  --out-memo ../docs_tmp/RLMs/PHASE_2_P5_LIVE_TIER1_MEMO_V1.md
```

Evaluate:
```bash
python scripts/evaluate_rlm_phase2_p5.py \
  --results-json ../docs_tmp/RLMs/PHASE_2_P5_LIVE_TIER1_RAW_V1.json \
  --report-json ../docs_tmp/RLMs/PHASE_2_P5_LIVE_TIER1_EVAL_V1.json
```

For partial-arm probes:
```bash
python scripts/evaluate_rlm_phase2_p5.py \
  --results-json <probe-json> \
  --allow-partial-arms \
  --report-json <probe-eval-json>
```

## 4) Required artifacts

At minimum:
- raw run JSON
- markdown summary table
- evaluator JSON report
- go/no-go memo

## 5) Stop conditions

Stop and investigate when any hold:
1. `nondeterminism_rate > 0` in evaluator.
2. `artifact_ok_rate < 1` for any enabled RLM arm.
3. safety caps trip unexpectedly (`aborted_for_spend_cap` or `aborted_for_token_cap`) before required cells are covered.
4. parity audit failures appear.

## 6) Notes

1. Some providers may not return reliable `cost_usd`; treat token cap as mandatory in that case.
2. Timeout values materially affect heavy scenario outcomes; calibrate per scenario family.
