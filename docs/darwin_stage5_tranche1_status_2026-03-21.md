# DARWIN Stage-5 Tranche-1 Status

Date: 2026-03-21
Status: Repo_SWE compounding protocol densified
References:
- `docs/darwin_stage5_tranche1_slice_2026-03-20.md`
- `docs/darwin_stage5_tranche1_canary_status_2026-03-20.md`
- `scripts/run_darwin_stage5_compounding_pilot_v0.py`
- `artifacts/darwin/stage5/tranche1/compounding_pilot_v0.json`

## What changed

- Repo_SWE Stage-5 repetition count is now held at `4` for tranche-1 protocol runs
- Stage-5 pilot summary now records:
  - valid comparison count
  - claim-eligible comparison count
  - provider-origin counts
  - fallback-reason counts
  - `reuse_lift` vs `no_lift` counts
- the Stage-5 live pilot has been rerun against the repo-root provider environment

## Current tranche-1 result

- `4` total arms
- `16` total runs
- `8` valid matched comparisons
- `8` claim-eligible matched comparisons
- `4` `CompoundingCaseV1` rows
- `2` `reuse_lift`
- `2` `no_lift`

## Current interpretation

The Stage-5 protocol is now denser than the initial canary and no longer purely negative.

Repo_SWE now shows a mixed but real warm-start vs family-lockout result. The tranche still does not prove scalable compounding. It does prove that the Stage-5 protocol can surface both positive and negative family-reuse outcomes under live-provider economics.

## Route/economics note

OpenRouter remains preferred in code. In the current workspace, live rows are mixed:

- some rows resolve through `openrouter/openai/gpt-5.4-mini`
- some rows fall back to direct OpenAI with `openrouter_http_401`

This is sufficient for Stage-5 tranche-1 economics truth. It is not yet clean OpenRouter-first economics.

## What this authorizes next

- write the tranche-1 review and hard gate
- keep Repo_SWE as the Stage-5 primary lane
- allow Systems to enter the next Stage-5 slice as a bounded secondary lane instead of canary-only, if the hard gate passes
