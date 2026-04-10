# DARWIN Stage 6 execution plan

Date: `2026-04-09`

Baseline:

- Stage 5 is complete on the integrated mainline.
- Stage 6 begins from the current Stage-5 family-aware and scaled-compounding surfaces.

## Tranche 0 — doctrine freeze

Objectives:

- freeze the Stage-6 thesis, anti-goals, and lane matrix
- freeze `FamilyActivationV1`, `ComparisonEnvelopeV1`, and `TransferCaseV2`
- keep composition explicitly gated

Hard gate:

- no Stage-5 artifact reopened as active work
- no new runtime truth
- no new lane authorized

## Tranche 1 — activation and economics truth

Objectives:

- make family activation operational
- make route/provider segmentation canonical on claim-bearing rows
- tighten comparison truth for broader transfer
- move activation/transfer/comparison logic out of builder-centric scripts into operational extension code

Expected first slice:

- implement `FamilyActivationV1`
- implement `ComparisonEnvelopeV1`
- implement `TransferCaseV2`
- extend compounding cases for `single_family_lockout`
- run one Systems-primary broader-transfer canary with Repo_SWE challenge and Scheduling confirmation

Current tranche-1 slice status:

- first operational slice is landed in:
  - `breadboard_ext/darwin/stage6.py`
  - `scripts/run_darwin_stage6_broader_transfer_canary_v0.py`
  - `docs/internals/DARWIN_STAGE6_TRANCHE1_STATUS_2026-04-09.md`
- Tranche 1 is now review-complete:
  - live/provider segmentation tightening is landed
  - `docs/internals/DARWIN_STAGE6_TRANCHE1_DIAGNOSIS_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE1_REVIEW_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE1_GATE_2026-04-09.md`
- Tranche-1 result:
  - live canary is technically clean
  - provider segmentation is canonical on claim-bearing rows
  - one positive activation probe now exists
  - broader transfer is beginning to become real without widening runtime truth

Hard gate:

- family activation is consumed by search policy
- provider/route segmentation is canonical on Stage-6 live rows
- at least one valid `single_family_lockout` comparison runs cleanly
- no replay/runtime regressions from the tightening work

## Tranche 2 — broader transfer becomes real

Objectives:

- make broader transfer real on the Systems-primary path
- keep Repo_SWE as a bounded challenge transfer surface
- make retained vs degraded vs invalid transfer outcomes typed and interpretable
- keep family demotion/holdback logic operational

Current tranche-2 slice status:

- first broader-transfer slice is landed in:
  - `breadboard_ext/darwin/stage6.py`
  - `scripts/run_darwin_stage6_broader_transfer_matrix_v0.py`
  - `docs/internals/DARWIN_STAGE6_TRANCHE2_STATUS_2026-04-09.md`
- Tranche 2 is now review-complete:
  - `docs/internals/DARWIN_STAGE6_TRANCHE2_REVIEW_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE2_GATE_2026-04-09.md`
- Tranche-2 result:
  - one Systems-primary retained transfer now exists
  - Repo_SWE remains a useful challenge transfer surface
  - failed-transfer taxonomy is now explicit and grounded in actual invalid transfer outcomes
  - provider segmentation remains canonical on claim-bearing rows

Hard gate:

- at least one retained or degraded-but-valid transfer exists
- failed-transfer taxonomy exists and is useful
- transfer scorecard exists and is reviewer-legible
- family-aware search remains interpretable under broader transfer breadth

## Tranche 3 — family-aware economics and broader compounding proof

Objectives:

- prove that family activation changes later search economics
- measure warm-start vs lockout vs single-family-lockout cleanly
- track promotion velocity and retained-transfer velocity under activation-aware search

Current tranche-3 slice status:

- first broader-compounding slice is landed in:
  - `breadboard_ext/darwin/stage6.py`
  - `scripts/run_darwin_stage6_broader_compounding_v0.py`
  - `docs/internals/DARWIN_STAGE6_TRANCHE3_STATUS_2026-04-09.md`
- Tranche 3 is now review-complete:
  - `docs/internals/DARWIN_STAGE6_TRANCHE3_REVIEW_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE3_GATE_2026-04-09.md`
- Tranche-3 result:
  - retained Systems-primary transfer now compounds positively on the bounded scheduling target
  - broader-compounding score, economics, linkage, replay, and registry surfaces now exist
  - `hold_single_retained_family_center` is the correct family-center decision
  - provider segmentation remains canonical through the retained-transfer base

Hard gate:

- at least one broader-compounding metric improves under the retained-transfer base
- family registry is operational, not ceremonial
- economics and comparison surfaces remain interpretable

## Tranche 4 — composition decision

Objectives:

- decide whether composition should open at all
- run one narrow composition canary only if prerequisites are satisfied

Current tranche-4 slice status:

- bounded composition-decision slice is landed in:
  - `scripts/build_darwin_stage6_composition_canary_v0.py`
  - `scripts/build_darwin_stage6_tranche4_family_registry_v0.py`
  - `scripts/build_darwin_stage6_tranche4_scorecard_v0.py`
  - `docs/internals/DARWIN_STAGE6_TRANCHE4_STATUS_2026-04-09.md`
- Tranche 4 is now review-complete:
  - `docs/internals/DARWIN_STAGE6_TRANCHE3_BASELINE_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE4_SLICE_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE4_REVIEW_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_TRANCHE4_GATE_2026-04-09.md`
- Tranche-4 result:
  - composition decision is now explicit:
    - `composition_not_authorized`
  - retained Systems-primary family remains the only stable Stage-6 center
  - tranche-4 family-registry and scorecard surfaces now carry the composition outcome forward

Hard gate:

- two or more stable families
- broader transfer matrix already positive
- no hidden support-envelope widening
- composition beats the best single-family control or is explicitly rejected

## Late comparative / pre-closeout

Objectives:

- consolidate Stage 6 into a canonical internal proof surface
- freeze the Stage-6 claim boundary
- isolate only final closeout work after this pass

Current pre-closeout slice status:

- pre-closeout surfaces are now landed in:
  - `scripts/build_darwin_stage6_canonical_artifact_index_v0.py`
  - `scripts/build_darwin_stage6_comparative_bundle_v0.py`
  - `docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_EXECUTION_PLAN_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_CLAIM_BOUNDARY_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_LATE_COMPARATIVE_MEMO_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_PRECLOSEOUT_REVIEW_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_PRECLOSEOUT_GATE_2026-04-09.md`
  - `docs/internals/DARWIN_STAGE6_REMAINING_TO_CLOSE_2026-04-09.md`
- Pre-closeout result:
  - canonical artifact index now exists
  - claim boundary is explicit and conservative
  - only closeout work remains after this pass

## Immediate repo-local next step

Proceed to final Stage-6 closeout only:

1. keep the retained Systems-primary family as the canonical center
2. treat the composition no-go as final Stage-6 evidence, not a deferred experiment
3. perform signoff, canonical freeze, and merged-state verification only

Do not reopen proving work before closeout.
