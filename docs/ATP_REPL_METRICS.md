# ATP REPL Metrics (Extraction & Dashboard Notes)

This doc describes how to capture p50/p95 latency and throughput summaries for
the Firecracker Lean REPL path and where the structured JSON is written.

Threshold policy source of truth: `config/atp_threshold_policy.json`
(schema `breadboard.atp.threshold_policy.v1`).

## Standardized ATP Metrics Surfaces
- Bench summary: `artifacts/atp_firecracker_repl_bench.json`
  - schema id: `breadboard.atp.bench_summary.v1` (documented fields)
  - key fields: `p50_ms`, `p95_ms`, `avg_ms`, `budget_p95_ms`, `budget_pass`
- Load summary: `artifacts/atp_firecracker_repl_load*.json`
  - schema id: `breadboard.atp.load_summary.v1` (documented fields)
  - key fields: `concurrency`, `iters`, `p50_ms`, `p95_ms`, `throughput_rps`, `budget_pass`
- State-ref recovery: `artifacts/atp_state_ref_recovery_report*.json`
  - schema id: `breadboard.atp.state_ref_recovery.v1`
  - key fields: `evaluation.classification`, `evaluation.ok`, `promotion.promoted`
- Snapshot pool stability: `artifacts/atp_snapshot_pool_stability_summary*.json`
  - schema id: `breadboard.atp.snapshot_pool_stability.v1`
  - key fields: `classification`, `run_count`, `p95_ms_max`, `ok`
- Consolidated ops digest: `artifacts/atp_ops_digest*.json`
  - schema id: `breadboard.atp.ops_digest.v1`
  - key fields: `decision_state`, `overall_ok`, trailing-failure counters
- Seven-day stability report: `artifacts/atp_seven_day_stability_report*.json`
  - schema id: `breadboard.atp.seven_day_stability.v1`
  - key fields: `classification`, `green_days`, `window_days`, `ok`

## Bench Summary (single-worker)
Run:
```
ATP_REPL_ENABLE=1 \
FIRECRACKER_SNAPSHOT=/path/to/lean.snap \
FIRECRACKER_SNAPSHOT_MEM=/path/to/lean.mem \
FIRECRACKER_SNAPSHOT_VSOCK=/path/to/vsock.sock \
FIRECRACKER_ROOTFS=/path/to/rootfs.ext4 \
python scripts/atp_firecracker_repl_bench.py
```

Output JSON is written to:
- `artifacts/atp_firecracker_repl_bench.json`
- override with `ATP_REPL_BENCH_SUMMARY_PATH`

Fields:
- `p50_ms`, `p95_ms`, `avg_ms`, `min_ms`, `max_ms`
- `budget_p95_ms`, `budget_pass`

## Thresholds
Both bench and load scripts honor `BUDGET_P95_MS` (default `200`).
Set `ATP_REPL_BUDGET_P95_MS` in CI and pass it through as `BUDGET_P95_MS`
to keep a single source of truth for alerting.

## Alert Thresholds (recommended)
Use these defaults unless you have a stronger baseline for your hardware:

- `BUDGET_P95_MS=200` for `scripts/atp_firecracker_repl_bench.py`.
- `BUDGET_P95_MS=200` for `scripts/atp_firecracker_repl_load_test.py`.
- `--max-regression-pct=10` for `scripts/check_atp_benchmark_drift.py`.

Expected failure behavior:

- Bench/load return non-zero when `budget_pass=false` (load exits with status `2`).
- Drift checker returns non-zero when any latency metric regresses beyond threshold.

Alerting policy:

- Treat one failure as a warning (investigate host load and snapshot integrity).
- Treat two consecutive nightly failures as an incident requiring snapshot refresh or rollback.
- Always compare measured-to-measured artifacts (never include warmup pass in drift baselines).

## Load Summary (multi-worker)
Run:
```
ATP_REPL_ENABLE=1 \
FIRECRACKER_SNAPSHOT=/path/to/lean.snap \
FIRECRACKER_SNAPSHOT_MEM=/path/to/lean.mem \
FIRECRACKER_SNAPSHOT_VSOCK=/path/to/vsock.sock \
FIRECRACKER_ROOTFS=/path/to/rootfs.ext4 \
CONCURRENCY=1 \
ITERS=40 \
python scripts/atp_firecracker_repl_load_test.py
```

Output JSON is written to:
- `artifacts/atp_firecracker_repl_load.json`
- override with `ATP_REPL_LOAD_SUMMARY_PATH`

Fields:
- `p50_ms`, `p95_ms`, `throughput_rps`, `duration_s`

Note: with a single snapshot directory, `CONCURRENCY>1` is not supported (the snapshot vsock UDS path is restore-sensitive).

## Minimal “Dashboard” Extraction
Example CLI extraction:
```
jq '{p50_ms, p95_ms, throughput_rps, budget_pass}' artifacts/atp_firecracker_repl_load.json
```

For a basic plot, load the JSON into any notebook and chart `p50_ms` / `p95_ms`
over time across run snapshots.

## Runtime Metrics Logging (JSONL)
Set `ATP_REPL_METRICS_PATH=/path/to/atp_repl_metrics.jsonl` to emit one JSON
line per REPL command executed via `/atp/repl` or `/atp/repl/batch`.

Fields:
- `request_id`, `success`
- `repl_ms`, `restore_ms`, `batch_size`
- `header_cache_hit`, `header_cache_miss`
- `ts` (unix seconds)

## Consolidated Ops Digest
Generate a single ATP operational status summary:
```
python scripts/atp_ops_digest.py \
  --artifact-dir artifacts \
  --threshold-policy config/atp_threshold_policy.json \
  --out artifacts/atp_ops_digest.latest.json
```

Decision states:
- `green`: latest required surfaces pass and no repeated-failure escalation.
- `warning`: latest run shows a failure but escalation threshold is not crossed.
- `incident`: trailing failure streak reached incident threshold from policy.
