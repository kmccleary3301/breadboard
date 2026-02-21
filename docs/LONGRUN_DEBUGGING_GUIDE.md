# LongRun Debugging Guide

## Debug Workflow
1. Inspect `meta/longrun_summary.json` first (stop reason + episode count + recovery metrics).
2. Correlate with `meta/longrun_state.json` (streaks, checkpoint index, queue snapshot).
3. Inspect `meta/episodes/longrun_episode_<n>.json` for per-episode completion + verification/reviewer details.
4. If rollback occurred, inspect `meta/checkpoints/latest_checkpoint.json` and corresponding checkpoint file.
5. Validate run summary parity block:
```bash
python scripts/check_longrun_run_summary_parity_audit.py <run_dir>/meta/run_summary.json
```

## Common Failure Classes
1. `queue_empty`
- Meaning: no pending queue item.
- Check queue backend source and claim/defer behavior.

2. `retry_budget_reached`
- Meaning: repeated failed episodes exhausted retry budget.
- Check provider errors, completion reasons, verification hard-fail chain.

3. `no_progress_threshold_reached`
- Meaning: repeated no-progress episodes crossed threshold.
- Check `no_progress_max_episodes` and signature streak metrics.

4. `provider_error_retry_exhausted`
- Meaning: provider exceptions exceeded backoff/retry policy.
- Check `recovery_metrics.provider_error_count` and `backoff_seconds`.

## Useful Validation Commands
```bash
pytest -q tests/test_longrun_flags.py tests/test_schema_v2_loader.py tests/test_longrun_controller.py tests/test_longrun_queue.py tests/test_longrun_recovery.py tests/test_longrun_verification.py tests/test_longrun_checkpoint.py tests/test_audit_longrun_parity_disabled.py tests/test_longrun_run_summary.py tests/test_check_longrun_run_summary_parity_audit.py tests/test_run_longrun_phase1_pilot.py
```

```bash
python scripts/audit_longrun_parity_disabled.py --repo-root . --json-out artifacts/longrun/longrun_parity_audit.json
```

## Known Limits / Non-Goals (Current Phase)
1. No production concurrency controller in this tranche.
2. Reviewer is a read-only hook, not a privileged autonomous executor.
3. Deterministic pilot is policy-focused; it does not benchmark live model quality.
4. LongRun remains opt-in and conservative by default.
