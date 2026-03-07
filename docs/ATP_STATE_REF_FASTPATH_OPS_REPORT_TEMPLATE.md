# ATP State-Ref Fastpath Ops Report Template

Use this template after each benchmark refresh window.

## 1) Inputs
- Baseline artifact: `<path>`
- Candidate measured artifact: `<path>`
- Drift threshold: `--max-regression-pct=<value>`
- Snapshot pool root: `<path>`
- Concurrency / ITERS / burn-in: `<c>/<iters>/<burn>`

## 2) Reproduction Commands
```bash
python scripts/atp_state_ref_regression_recovery.py \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --runs 3 \
  --required-consecutive-pass 2 \
  --max-regression-pct 10 \
  --report-out artifacts/atp_state_ref_recovery_report.latest.json

FIRECRACKER_SNAPSHOT_DIRS=/snap0,/snap1,/snap2,/snap3 \
CONCURRENCY=4 ITERS=10 BURN_IN_ITERS=2 REPL_TIMEOUT=120 \
ATP_STATE_REF_BENCH_SUMMARY_PATH=artifacts/atp_state_ref_fastpath_candidate.json \
python scripts/atp_state_ref_fastpath_bench.py

python scripts/validate_atp_artifacts.py \
  artifacts/atp_state_ref_fastpath_candidate.json

python scripts/check_atp_benchmark_drift.py \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --candidate artifacts/atp_state_ref_fastpath_candidate.json \
  --max-regression-pct 10 \
  --out artifacts/atp_benchmark_drift_report.latest.json

# Optional (only when intentionally comparing different run shapes):
# python scripts/check_atp_benchmark_drift.py \
#   --baseline artifacts/atp_state_ref_fastpath_baseline.json \
#   --candidate artifacts/atp_state_ref_fastpath_candidate.json \
#   --max-regression-pct 10 \
#   --allow-config-mismatch \
#   --out artifacts/atp_benchmark_drift_report.latest.json
```

Note: drift checks are strict by default and fail on run-shape mismatches
(`concurrency`, `iters_per_worker`, `burn_in_iters_per_worker`,
`file_io_b64_active_all_workers`, and worker count).

## 3) Result Summary
- Drift status: `PASS | FAIL`
- `repl_ms_avg_across_workers`: `<value>`
- `unpickle_s_avg_across_workers`: `<value>`
- `request_wall_s_avg_across_workers`: `<value>`
- `request_wall_s_p95_across_workers`: `<value>`
- `file_io_b64_active_all_workers`: `true | false`

## 4) Decision
- Promote candidate to baseline: `YES | NO`
- Reason:
  - `YES`: no metric exceeds threshold and sample depth is valid.
  - `NO`: threshold breach, invalid sample depth, shape mismatch, or suspected host noise.

Recovery classification guidance:
- `stable_pass`: candidate stream is stable; promotion can proceed if shape matches baseline.
- `unstable_mixed`: do not promote; rerun measured-only once, then investigate host/snapshot noise.
- `persistent_fail`: do not promote; escalate and run snapshot/host diagnostics.

## 5) Baseline Promotion Command (if YES)
```bash
python scripts/promote_atp_state_ref_baseline.py \
  --candidate artifacts/atp_state_ref_fastpath_candidate.json \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --meta-out artifacts/atp_state_ref_fastpath_baseline.meta.json
```

## 6) Follow-up Actions
- If `FAIL`: rerun measured-only pass once to rule out host noise.
- If repeated `FAIL`: investigate snapshot integrity and host contention.
- Generate/attach variance trend report for recent measured runs:
  - `python scripts/atp_state_ref_variance_report.py --glob "artifacts/atp_state_ref_fastpath_*_measured*.json" --limit 20 --out artifacts/atp_state_ref_variance_report.latest.json`
- Attach:
  - candidate JSON
  - drift report JSON
  - recovery report JSON
  - variance report JSON
  - preflight output (`scripts/atp_snapshot_preflight.py --json`)
