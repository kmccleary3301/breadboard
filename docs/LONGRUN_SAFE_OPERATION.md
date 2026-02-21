# LongRun Safe Operation Guide

## Purpose
This guide covers safe operational use of BreadBoard LongRun mode for bounded, resumable multi-episode execution.

## Preconditions
1. Keep parity/E4 configs with `long_running.enabled: false`.
2. Enable LongRun only in dedicated longrun profiles/configs.
3. Set explicit budgets:
- episode cap
- wall-clock cap
- retry/no-progress bounds
4. Keep verification tiers bounded with finite timeouts.

## Minimum Safe Config Pattern
```yaml
long_running:
  enabled: true
  episode:
    max_steps_override: 20
  budgets:
    max_episodes: 6
    max_wall_clock_seconds: 1800
    max_retries_per_item: 2
  recovery:
    no_progress_max_episodes: 2
    no_progress_signature_repeats: 2
    rollback_enabled: true
    checkpoint_every_episodes: 1
```

## Runtime Artifacts To Monitor
- `meta/longrun_state.json`
- `meta/longrun_summary.json`
- `meta/longrun_queue_snapshot.json`
- `meta/checkpoints/latest_checkpoint.json`
- `meta/episodes/longrun_episode_<n>.json`
- `meta/reviewer/reviewer_ep_<n>.json` (if reviewer enabled)
- `meta/run_summary.json` (`longrun.parity_audit`)

## Hard Safety Rules
1. Never run LongRun without explicit caps.
2. Never enable LongRun in parity/golden replay configs.
3. Keep reviewer in `read_only` mode only.
4. Keep rollback/checkpoint cadence small and bounded.
5. Treat provider-error retries as bounded recovery, not infinite healing.

## Immediate Disable / Rollback
1. Set `BREADBOARD_LONGRUN_ENABLE=0`.
2. Ensure config has `long_running.enabled: false`.
3. Re-run parity-disable audit:
```bash
python scripts/audit_longrun_parity_disabled.py --repo-root . --json-out artifacts/longrun/longrun_parity_audit.json
```
4. Verify output shows `"ok": true`.

## Live Provider Pilot (Spend-Capped)
Use this only after deterministic gates are green and credits are approved.

1. Run a small paired baseline-vs-longrun pilot with hard caps:
```bash
python scripts/run_longrun_phase2_live_pilot.py \
  --baseline-config agent_configs/base_v2.yaml \
  --longrun-config agent_configs/longrun_conservative_v1.yaml \
  --out-json artifacts/longrun/live/longrun_phase2_live_pilot_v1.json \
  --out-markdown artifacts/longrun/live/longrun_phase2_live_pilot_v1.md \
  --workspace-root tmp/longrun_phase2_live_runs \
  --max-pairs 3 \
  --max-total-tokens 40000 \
  --max-total-estimated-cost-usd 2.0 \
  --arm-timeout-seconds 180
```

`--arm-timeout-seconds` is a fail-open safety guard: if an arm stalls, the runner records a structured failed arm row and continues writing a final artifact.

2. Validate the artifact shape and cap compliance:
```bash
python scripts/validate_longrun_phase2_live_artifacts.py \
  --json artifacts/longrun/live/longrun_phase2_live_pilot_v1.json
```
By default, validation rejects "live" artifacts with zero aggregate token usage.

3. Keep this lane manual-only. Do not wire it to default CI triggers.

## Related Docs
- `docs/LONGRUN_DEBUGGING_GUIDE.md`
- `docs/LONGRUN_PROFILE_PRESETS.md`
- `docs/LONGRUN_POLICY_PACKS.md`
- `docs/LONGRUN_PHASE2_METHODOLOGY_APPENDIX.md`
- `docs/LONGRUN_PHASE2_LIMITATIONS_AND_THREAT_MODEL.md`
