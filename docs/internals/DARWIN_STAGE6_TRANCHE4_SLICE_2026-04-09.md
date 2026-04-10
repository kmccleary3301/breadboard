# DARWIN Stage 6 tranche-4 composition-decision slice

Date: `2026-04-09`

Objective:

- make one bounded Stage-6 composition decision against the retained Tranche-3 family center
- keep the result explicit and typed
- avoid turning Tranche 4 into a broad composition program

Hard boundaries:

- one composition canary at most
- no new lanes
- no broad family-composition sweep
- no runtime-truth widening
- no closeout work mixed into the experiment slice itself

Decision classes:

- `composition_positive`
- `composition_neutral`
- `composition_negative`
- `composition_invalid`
- `composition_not_authorized`

Current decision basis:

- retained Systems-primary family is the only stable proving center
- Repo_SWE remains challenge context only
- held-back Repo_SWE tool-scope family remains invalid for activation and composition
- composition is only authorized if it can be compared directly against the retained single-family control without confounding the baseline

Expected outputs:

- one composition-canary artifact
- one tranche-4 family-registry update
- one tranche-4 scorecard update
- tranche-4 status, review, and gate docs
