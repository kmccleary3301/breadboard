# ATP Firecracker REPL Runbook

This runbook covers the operational steps for running ATP REPL (`/atp/repl`
and `/atp/v1/repl`), validating performance, and maintaining snapshot hygiene.

Machine-readable ATP error codes are documented in `docs/ATP_REPL_ERRORS.md`.
Canonical vsock envelope and legacy fallback policy is documented in `docs/ATP_VSOCK_PROTOCOL_POLICY.md`.

Contract versioning:
- ATP REPL request/response payloads carry `api_version=atp.repl.v1`.
- `/atp/v1/*` endpoints are versioned aliases with the same behavior as `/atp/*`.
- ATP REPL failures include `harness_diagnostic` with normalized classification/action.

## Required Environment
- `ATP_REPL_ENABLE=1`
- `FIRECRACKER_SNAPSHOT=/path/to/lean.snap`
- `FIRECRACKER_SNAPSHOT_MEM=/path/to/lean.mem`
- `FIRECRACKER_SNAPSHOT_VSOCK=/path/to/vsock.sock`
- `FIRECRACKER_ROOTFS=/path/to/rootfs.ext4`
- Optional: `FIRECRACKER_BIN=/usr/local/bin/firecracker`
- Optional: `FIRECRACKER_VSOCK_PORT=52`
- Optional (REPL-only override): `FIRECRACKER_REPL_VSOCK_PORT=52`

## Smoke Test
```
python -m pytest -q tests/test_sandbox_firecracker.py::test_firecracker_repl_smoke
```

## Benchmark (p50/p95)
```
ATP_REPL_ENABLE=1 \
FIRECRACKER_SNAPSHOT=/path/to/lean.snap \
FIRECRACKER_SNAPSHOT_MEM=/path/to/lean.mem \
FIRECRACKER_SNAPSHOT_VSOCK=/path/to/vsock.sock \
FIRECRACKER_ROOTFS=/path/to/rootfs.ext4 \
python scripts/atp_firecracker_repl_bench.py
```
Summary JSON: `artifacts/atp_firecracker_repl_bench.json`

## State-Ref Fast-Path Benchmark (stable protocol)
Use this when comparing `state_ref` + `file_io_b64` performance over time.

State-ref lifecycle policy:
- `new_state_ref` is emitted only when `want_state=true` and the request succeeds.
- `state_ref` reuse is content-addressed and idempotent for identical bytes+metadata.
- `tenant_id` (when provided) is persisted in CAS metadata and enforced on restore.
- Corrupt or malformed references return machine code `state_ref_corrupt`.
- Missing refs return `state_ref_not_found`; incompatible refs return `state_ref_incompatible`.
- Cross-tenant restore attempts return `state_ref_tenant_mismatch`.

## ATP Replay Eval Fixture Generation
Generate deterministic ATP replay/eval fixture bundles from captured ATP batch
responses:

```
python scripts/generate_atp_replay_eval_fixture.py \
  --batch-json tests/fixtures/atp_replay/batch_round_001.json \
  --out-dir artifacts/atp_replay_eval_fixture \
  --fixture-id batch_round_001 \
  --round-index 1 \
  --timestamp 1700000000
```

Outputs:
- `atp_batch_response.json`
- `atp_anchor_rows.jsonl`
- `replay_eval_candidates.json`
- `fixture_manifest.json`

Recommended protocol:
- Run one warmup pass and discard it.
- Report only the measured pass.
- Use `ITERS>=10` for measured runs.
- Compare measured-to-measured only (not first-run-to-second-run).

One-command runner:
```
FIRECRACKER_SNAPSHOT_DIRS=/snap0,/snap1,/snap2,/snap3 \
CONCURRENCY=4 \
WARMUP_ITERS=3 \
MEASURE_ITERS=10 \
MEASURE_BURN_IN_ITERS=2 \
scripts/run_atp_state_ref_fastpath_stable.sh
```

Direct benchmark script (single pass):
```
FIRECRACKER_SNAPSHOT_DIRS=/snap0,/snap1,/snap2,/snap3 \
CONCURRENCY=4 \
ITERS=10 \
BURN_IN_ITERS=2 \
python scripts/atp_state_ref_fastpath_bench.py
```

Default summary JSON: `artifacts/atp_state_ref_fastpath_bench.json`

Ops reporting template: `docs/ATP_STATE_REF_FASTPATH_OPS_REPORT_TEMPLATE.md`

Canonical repo baseline (for drift checks): `artifacts/atp_state_ref_fastpath_baseline.json`

Latest promoted baseline (2026-02-12 UTC):
- Source candidate: `artifacts/atp_state_ref_fastpath_c4_20260211-231831_measured_r2.json`
- Recovery status: `stable_pass` (`pass_count=4`, `fail_count=0`, `required_consecutive_pass=2`)
- Metrics at promotion:
  - `repl_ms_avg_across_workers=380.3513`
  - `unpickle_s_avg_across_workers=0.7189`
  - `request_wall_s_avg_across_workers=0.7640`
  - `request_wall_s_p95_across_workers=0.7825`

Promote a measured artifact to baseline:
```
python scripts/promote_atp_state_ref_baseline.py \
  --candidate artifacts/atp_state_ref_fastpath_c4_YYYYMMDD-HHMMSS_measured.json \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --meta-out artifacts/atp_state_ref_fastpath_baseline.meta.json
```

Run drift against canonical baseline:
```
python scripts/check_atp_benchmark_drift.py \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --candidate artifacts/atp_state_ref_fastpath_c4_YYYYMMDD-HHMMSS_measured.json \
  --max-regression-pct 10 \
  --out artifacts/atp_benchmark_drift_report.latest.json
```

Close-loop recovery (repeat measured runs + deterministic decision):
```
FIRECRACKER_SNAPSHOT_DIRS=/snap0,/snap1,/snap2,/snap3 \
CONCURRENCY=4 \
ITERS=10 \
BURN_IN_ITERS=2 \
python scripts/atp_state_ref_regression_recovery.py \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --runs 3 \
  --required-consecutive-pass 2 \
  --max-regression-pct 10 \
  --report-out artifacts/atp_state_ref_recovery_report.latest.json
```

Recovery classifications:
- `stable_pass`: at least `required_consecutive_pass` measured runs passed drift checks consecutively.
- `unstable_mixed`: some runs passed and some failed, with no qualifying consecutive pass streak.
- `persistent_fail`: all measured runs failed drift checks.
- `no_runs`: no candidate artifacts were evaluated.

Operator policy for `unstable_mixed`:
- Do not promote.
- Capture one additional measured-only pass and run drift check again.
- If instability persists, inspect snapshot pool health and host contention before retrying.

Optional auto-promote when stable pass criteria is met:
```
python scripts/atp_state_ref_regression_recovery.py \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --runs 3 \
  --required-consecutive-pass 2 \
  --max-regression-pct 10 \
  --promote-on-pass \
  --baseline-out artifacts/atp_state_ref_fastpath_baseline.json \
  --meta-out artifacts/atp_state_ref_fastpath_baseline.meta.json
```

Auto-promotion safety gates:
- Promotion is blocked when run-shape metadata does not match baseline (`shape_mismatch`).
- Promotion is blocked when candidate sample depth is below recommended (`sample_depth_insufficient`).
- Baseline metadata (`*.meta.json`) is required and validated for completeness.

Variance trend report (recent measured runs):
```
python scripts/atp_state_ref_variance_report.py \
  --glob "artifacts/atp_state_ref_fastpath_*_measured*.json" \
  --limit 20 \
  --baseline artifacts/atp_state_ref_fastpath_baseline.json \
  --out artifacts/atp_state_ref_variance_report.latest.json
```

## Load Test (manual)
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
Summary JSON: `artifacts/atp_firecracker_repl_load.json`

Note: Firecracker snapshot restores are path-sensitive (including the host-side vsock UDS path), so a single
snapshot directory cannot be used to restore multiple VMs concurrently.

To test true multi-VM concurrency, build one snapshot per worker (distinct `SNAP_DIR`/`vsock.sock`) and run:
```
ATP_REPL_ENABLE=1 \
FIRECRACKER_SNAPSHOT=/path/to/any/lean.snap \
FIRECRACKER_SNAPSHOT_DIRS=/snapA,/snapB,/snapC \
CONCURRENCY=3 \
ITERS=60 \
python scripts/atp_firecracker_repl_load_test.py
```
`FIRECRACKER_SNAPSHOT_DIRS` must include `lean.snap`, `lean.mem`, and `rootfs.ext4` per directory.
Use comma-separated paths as the canonical format; `:` is accepted for backward compatibility on Linux.
`vsock.sock` may be absent at rest; if present it must be a socket path.

## Load Test (nightly target)
Single snapshot directory:
```
ATP_REPL_ENABLE=1 \
FIRECRACKER_SNAPSHOT=/path/to/lean.snap \
FIRECRACKER_SNAPSHOT_MEM=/path/to/lean.mem \
FIRECRACKER_SNAPSHOT_VSOCK=/path/to/vsock.sock \
FIRECRACKER_ROOTFS=/path/to/rootfs.ext4 \
ATP_REPL_LOAD_CONCURRENCY=1 \
ATP_REPL_LOAD_ITERS=40 \
scripts/atp_firecracker_repl_load_nightly.sh
```

Snapshot pool (multi-worker):
```
ATP_REPL_ENABLE=1 \
FIRECRACKER_SNAPSHOT_DIRS=/snapA,/snapB,/snapC \
ATP_REPL_LOAD_CONCURRENCY=3 \
ATP_REPL_LOAD_ITERS=60 \
scripts/atp_firecracker_repl_load_nightly.sh
```

Summary JSON default: `artifacts/atp_firecracker_repl_load_YYYYMMDD.json`

### Nightly alert trigger rules
- Set `BUDGET_P95_MS` (or pass through `ATP_REPL_BUDGET_P95_MS`) to enforce a hard latency budget.
- If the nightly load job exits non-zero, treat it as a warning and inspect snapshot health first.
- If two consecutive nightly runs fail budget checks, open an incident and refresh or roll back the snapshot baseline.

## Snapshot Rotation Policy
- Keep the last **N** snapshots locally (default: 3).
- Archive stable snapshots to long-term storage before pruning.
- Snapshot naming convention: `/tmp/fc_lean_snap_ready-<timestamp>/`.

## Snapshot Preflight Validation
Validate snapshot inputs before running smoke/bench/load jobs:
```
python scripts/atp_snapshot_preflight.py \
  --snapshot-dir /path/to/snapshot_dir
```
The script verifies `lean.snap`, `lean.mem`, and `rootfs.ext4`; for `vsock.sock`,
it accepts either a missing path (rest state) or a socket path (live/recently used state).
It exits non-zero when required artifacts are missing or invalid.

## Snapshot Pool Health Selection
When using `FIRECRACKER_SNAPSHOT_DIRS`, validate and select healthy directories:
```
python scripts/atp_snapshot_pool_health.py \
  --snapshot-dirs /snapA,/snapB,/snapC \
  --probe-restore \
  --min-healthy 1 \
  --print-export
```
This prints an `export FIRECRACKER_SNAPSHOT_DIRS=...` line containing only healthy
snapshot dirs.

## Deterministic Snapshot Pool Build + Verify
Build a new generation from a known-good source snapshot (copy mode):
```
python scripts/atp_snapshot_pool_builder.py build \
  --pool-root ../lean_snapshot \
  --generation gen-YYYYMMDD-HHMMSS \
  --workers 4 \
  --source-dir /path/to/source_snapshot \
  --keep-generations 3
```

Verify checksums and required files:
```
python scripts/atp_snapshot_pool_builder.py verify \
  --generation-dir ../lean_snapshot/gen-YYYYMMDD-HHMMSS
```

Rotate old generations (dry-run by default):
```
python scripts/atp_snapshot_pool_builder.py rotate \
  --pool-root ../lean_snapshot \
  --keep-generations 3
```

Apply rotation:
```
python scripts/atp_snapshot_pool_builder.py rotate \
  --pool-root ../lean_snapshot \
  --keep-generations 3 \
  --apply
```

Pre-build disk budget fail-fast policy:
- Default builder policy requires `workers * estimated_gb_per_worker + reserve_gb` free space.
- Tune with `--estimated-gb-per-worker` and `--reserve-gb`.
- Builder also checks and removes stale per-worker `vsock.sock` files when safe.

Worker lock-file reuse protection:
- `ATP_SNAPSHOT_POOL_LOCK=1` (default) acquires per-worker lock files during load/state-ref runs.
- Lock name override: `ATP_SNAPSHOT_POOL_LOCK_NAME` (default `.bb_snapshot_pool.lock`).
- This prevents accidental concurrent reuse of the same worker snapshot dirs across jobs.

## Vsock Protocol Conformance Across Snapshot Generations
Validate protocol readiness markers and port consistency across all discovered
snapshot generations under a root directory:
```
python scripts/atp_vsock_protocol_conformance.py \
  --snapshot-root ../lean_snapshot \
  --out artifacts/atp_vsock_protocol_conformance.latest.json
```
Expected pass criteria:
- every discovered snapshot has `Mathlib ready for snapshot` in `serial.log`
- every discovered snapshot reports `Vsock REPL agent running with pid=...`
- detected REPL port is consistent and matches expected port (default `52`)

Script-level gate (optional):
- Set `ATP_SNAPSHOT_POOL_PROBE=1` to enable restore probes in:
  - `scripts/atp_firecracker_ci.sh`
  - `scripts/atp_firecracker_repl_load_nightly.sh`
  - `scripts/run_atp_state_ref_fastpath_stable.sh`
- Set `ATP_SNAPSHOT_POOL_MIN_HEALTHY` to require more than one healthy dir.

## Snapshot Rebuild (external pipeline)
1) Trigger the external snapshot pipeline (outside this repo).
2) Verify output directory matches `/tmp/fc_lean_snap_ready-<timestamp>/`.
3) Confirm required artifacts exist: `lean.snap`, `lean.mem`, `rootfs.ext4`.
   `vsock.sock` is optional at rest.
4) Run the REPL smoke test and bench using the new snapshot paths.

## Snapshot Cleanup Scheduling
- Dry-run: `python scripts/atp_snapshot_cleanup.py --keep 3`
- Apply deletion: `python scripts/atp_snapshot_cleanup.py --keep 3 --apply`
- Recommended: run daily/weekly via cron or a CI schedule.

## Concurrency-4 Stability Summary
Aggregate repeated load-run artifacts and enforce stability policy:
```
python scripts/atp_snapshot_pool_stability.py \
  --glob "artifacts/atp_firecracker_repl_load*.json" \
  --require-concurrency 4 \
  --min-runs 3 \
  --max-p95-ms 200 \
  --out artifacts/atp_snapshot_pool_stability_summary.latest.json
```

Classification semantics:
- `stable_pass`: enough runs, within p95 budget, and failure count within threshold.
- `insufficient_runs`: not enough matching concurrency runs collected yet.
- `unstable_mixed`: mixed pass/fail outcomes within the window.
- `persistent_fail`: repeated failures with no passing window.

## Consolidated Ops Digest
Build an ATP-wide operational digest from bench/load/recovery/stability artifacts:
```
python scripts/atp_ops_digest.py \
  --artifact-dir artifacts \
  --threshold-policy config/atp_threshold_policy.json \
  --out artifacts/atp_ops_digest.latest.json
```

Decision states:
- `green`: no current actionable failures.
- `warning`: latest run failed but repeated-failure threshold has not been crossed.
- `incident`: trailing failures crossed threshold (`consecutive_fail_incident`) and require escalation.

## Seven-Day Stability Report
Generate and require a rolling seven-day ATP stability report:
```
python scripts/atp_seven_day_stability_report.py \
  --artifact-dir artifacts \
  --threshold-policy config/atp_threshold_policy.json \
  --out artifacts/atp_seven_day_stability_report.latest.json
```

Report classifications:
- `stable_window`: all required days satisfy bench/load/recovery/stability gates.
- `partial_window`: some days are green, but window criteria not fully satisfied.
- `failed_window`: no day in the window satisfies complete gates.

## Incident Decision Tree
Use `decision_state` from ops digest to drive operator action:
- `green`: continue nightly monitoring.
- `warning`: rerun once, inspect snapshot health and host contention, then compare drift.
- `incident`: open incident ticket, freeze promotion, run rollback/refresh path for snapshot baseline.

Escalation rule:
- Incident triggers when trailing failure streak reaches policy thresholds in `config/atp_threshold_policy.json`.

## REPL Outage Playbook (`/atp/repl` 503 or timeout)
1) Verify snapshot files exist and are readable (`lean.snap`, `lean.mem`, `rootfs.ext4`),
   and that `vsock.sock` is either absent or a valid socket path.
2) Check for a live Firecracker process using the same vsock UDS; if none, remove stale `vsock.sock`.
3) Re-run `scripts/atp_firecracker_repl_bench.py` and confirm p95 is within budget.
4) If needed, roll back to the last known-good snapshot directory.
5) If the outage persists, temporarily disable `ATP_REPL_ENABLE` and surface a status banner.
6) Capture recent `/atp/repl` metrics (if `ATP_REPL_METRICS_PATH` is enabled) for postmortem.

## Convenience: activate snapshot + run checks
From the repo root (`/shared_folders/querylake_server/ray_testing/ray_SCE`):
```
./activate_lean_snapshot.sh --ci
```
To export the snapshot env vars into your current shell:
```
source <(./activate_lean_snapshot.sh --print-exports)
```
