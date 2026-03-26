# DARWIN Stage-4 Promotion Entry Gate

Date: 2026-03-20
Status: passed for multi-family promotion entry
References:
- `docs/darwin_stage4_deep_live_search_review_2026-03-20.md`
- `docs/darwin_stage4_deep_live_search_status_2026-03-20.md`
- `docs/darwin_stage4_replay_note_2026-03-20.md`

## Gate question

Can Stage 4 now leave deep bounded live search and enter the multi-family promotion tranche?

## Decision

Yes.

## What is now satisfied

- repeated live campaigns completed on both primary lanes
- claim-eligible comparisons remain valid across both primary lanes
- stable strongest-family signals exist on both primary lanes
- replay-backed strongest-family checks passed on both primary lanes
- invalidity remains controlled and interpretable

## What remains blocked

- transfer claims
- broad compounding claims
- public comparative claims
- broad provider-economics claims

## Next authorized move

The next Stage-4 tranche is multi-family promotion on:

- `lane.repo_swe`
- `lane.systems`

with `lane.harness` as watchdog and `lane.scheduling` only as bounded confirmation support if needed.
