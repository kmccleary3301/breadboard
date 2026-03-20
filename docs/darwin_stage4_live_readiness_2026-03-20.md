# DARWIN Stage-4 Live Readiness

Date: 2026-03-20
Status: live provider path verified; claims still blocked pending pricing semantics
References:
- `scripts/build_darwin_stage4_live_readiness_v0.py`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`
- `docs/darwin_stage4_live_economics_status_2026-03-20.md`

## Current state

The Stage-4 repo_swe live-economics pilot exists and runs cleanly, but the workspace is not yet eligible for live-provider claims.

## Current blocking condition

- `DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M` is absent
- `DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M` is absent

## What this means

- the Stage-4 repo_swe pilot can now execute live provider calls when `.env` is sourced and `DARWIN_STAGE4_ENABLE_LIVE=1`
- no Stage-4 comparison row is currently live-claim-eligible because the provider path is returning usage but not provider-backed cost
- the SearchPolicy pilot is valid as a bounded selection/control surface, but not yet as live power evidence

## Unblocking command shape

```bash
set -a && source .env && export DARWIN_STAGE4_ENABLE_LIVE=1 && set +a
export DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M=...
export DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M=...
python scripts/run_darwin_stage4_live_economics_pilot_v0.py --json
python scripts/build_darwin_stage4_live_readiness_v0.py --json
```
