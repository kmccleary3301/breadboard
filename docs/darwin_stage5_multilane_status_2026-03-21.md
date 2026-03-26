# DARWIN Stage-5 Multi-Lane Operational Status

Date: 2026-03-21
Status: repeated multi-lane tranche landed
References:
- `docs/darwin_stage5_multilane_slice_2026-03-21.md`
- `artifacts/darwin/stage5/multilane/multilane_compounding_v0.json`

## What landed

- a repeated multi-lane Stage-5 runner across:
  - `lane.repo_swe`
  - `lane.systems`
- shared round-level aggregation of:
  - valid comparison counts
  - claim-eligible comparison counts
  - `reuse_lift`
  - `no_lift`
  - provider-origin counts
  - fallback-reason counts

## Current repeated multi-lane result

Across `2` rounds:

- Repo_SWE:
  - `16` claim-eligible comparisons
  - `2` `reuse_lift`
  - `6` `no_lift`
- Systems:
  - `16` claim-eligible comparisons
  - `6` `reuse_lift`
  - `2` `no_lift`

## Interpretation

Stage 5 now has a repeated multi-lane compounding surface instead of single-lane point results only.

The current tranche is still bounded and still mixed, but it is already informative:

- Repo_SWE remains the primary lane and still shows positive reuse cases, but its current repeated results are less stable
- Systems remains secondary, but under the current bounded slice it is showing stronger repeated warm-start advantage than Repo_SWE

This does not prove scalable compounding yet. It does prove that the Stage-5 compounding protocol now produces repeated, lane-differentiated outcomes under one shared review surface.

## Route/economics note

Current live rows still show mixed provider resolution:

- OpenRouter remains preferred in code
- direct OpenAI fallback still occurs after `openrouter_http_401`

The current result is good enough for bounded Stage-5 protocol work. It is not yet a clean economics proof.

## What this authorizes next

- a Stage-5 review/gate for repeated multi-lane compounding
- tighter SearchPolicyV2 review over cross-lane family allocation
- continued bounded Stage-5 scaling before any new transfer or composition step
