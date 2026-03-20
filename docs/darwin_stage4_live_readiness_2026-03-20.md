# DARWIN Stage-4 Live Readiness

Date: 2026-03-20
Status: blocked pending provider readiness
References:
- `scripts/build_darwin_stage4_live_readiness_v0.py`
- `artifacts/darwin/stage4/live_economics/live_readiness_v0.json`
- `docs/darwin_stage4_live_economics_status_2026-03-20.md`

## Current state

The Stage-4 repo_swe live-economics pilot exists and runs cleanly, but the workspace is not yet eligible for live-provider claims.

## Blocking conditions

- `OPENAI_API_KEY` is absent
- `OPENROUTER_API_KEY` is absent
- `DARWIN_STAGE4_ENABLE_LIVE` is absent
- `DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M` is absent
- `DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M` is absent

## What this means

- the current Stage-4 pilot remains scaffold-only in this workspace
- no Stage-4 comparison row is currently live-claim-eligible
- the SearchPolicy pilot is valid as a bounded selection/control surface, but not yet as live power evidence

## Unblocking command shape

```bash
export DARWIN_STAGE4_ENABLE_LIVE=1
export OPENROUTER_API_KEY=...
export DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M=...
export DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M=...
python scripts/run_darwin_stage4_live_economics_pilot_v0.py --json
```
