# DARWIN Stage 6 tranche-4 status

Date: `2026-04-09`

Scope landed in this slice:

- Stage-6 composition decision is now explicit in:
  - `scripts/build_darwin_stage6_composition_canary_v0.py`
  - `scripts/build_darwin_stage6_tranche4_family_registry_v0.py`
  - `scripts/build_darwin_stage6_tranche4_scorecard_v0.py`
- Stage-6 now emits:
  - composition canary artifact
  - tranche-4 family-registry update
  - tranche-4 scorecard update
  - pre-closeout canonical artifact index
  - pre-closeout comparative bundle

Current read:

- composition result = `composition_not_authorized`
- composition pair remains bounded to the retained Systems-primary family plus the Repo_SWE challenge family
- the result is a valid no-go, not a missing experiment:
  - the Repo_SWE challenge family remains `challenge_context_only`
  - opening composition here would confound the retained single-family proving baseline
- retained Systems-primary family remains the correct Stage-6 center:
  - `hold_single_retained_family_center`
- provider segmentation and comparison surfaces remain unchanged and canonical under the carried-forward baseline

Artifacts:

- `artifacts/darwin/stage6/tranche4/composition_canary/composition_canary_v0.json`
- `artifacts/darwin/stage6/tranche4/family_registry/family_registry_v0.json`
- `artifacts/darwin/stage6/tranche4/scorecard/scorecard_v0.json`
- `artifacts/darwin/stage6/precloseout/canonical_artifact_index_v0.json`
- `artifacts/darwin/stage6/precloseout/comparative_bundle_v0.json`

What is complete in this slice:

- composition is now an explicit Stage-6 decision surface
- the retained single-family center remains the canonical baseline
- challenge-lane relevance after the composition decision is explicit
- Stage-6 now has a clean bridge into pre-closeout work

What is not yet complete:

- final pre-closeout review and gate are still ahead
- final Stage-6 closeout and merged-state verification are still ahead
