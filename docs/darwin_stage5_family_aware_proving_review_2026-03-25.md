# DARWIN Stage-5 Family-Aware Proving Review

Date: 2026-03-25
Status: reviewed
References:
- `docs/darwin_stage5_family_aware_proving_completion_slice_2026-03-25.md`
- `docs/darwin_stage5_repo_swe_challenge_refresh_status_2026-03-25.md`
- `docs/darwin_stage5_systems_confirmation_status_2026-03-25.md`
- `docs/darwin_stage5_family_aware_scorecard_status_2026-03-25.md`
- `artifacts/darwin/stage5/compounding_quality/compounding_quality_v0.json`

## Review questions

### 1. Is Systems still the correct primary proving lane?

Yes.

Systems remains the cleaner Stage-5 lane under the current bounded family-aware surface:

- `24` claim-eligible comparisons in the systems-weighted live review
- `7` reuse-lift vs `5` no-lift in the current proving summary
- a clean completed live-run status

### 2. Is Repo_SWE challenge evidence now fresh enough?

Yes.

Repo_SWE is no longer stale:

- family surface: `settled_topology`
- challenge refresh completion: `complete`
- live claim surface: `claim_eligible_live`

Repo_SWE is still weaker than Systems, but the challenge lane does not need to be stronger. It needs to be current and interpretable.

### 3. Is family-aware policy consumption current and real?

Yes.

`SearchPolicyV2` now consumes:

- cross-lane proving weight
- Repo_SWE family-surface status
- probe blocking when the family surface is stale

That means active allocation is now tied to current family-surface integrity rather than only to stale historical artifacts.

### 4. Is warm-start vs family-lockout more credible than before?

Yes.

The proving surface is materially cleaner than it was before this slice:

- systems-weighted live status is now `complete`
- Repo_SWE family state is now `settled_topology`
- the scorecard and compounding-quality summaries are built from fresh inputs

### 5. Is Stage 5 ready to leave Tranche 2?

Yes.

Stage-5 is ready to leave proving-integrity repair and enter scaled compounding planning, while still keeping transfer and family composition closed until that next tranche is explicitly defined.

## Review conclusion

The family-aware proving tranche is passed.

The proving split remains:

- `lane.systems` = primary proving lane
- `lane.repo_swe` = challenge lane

That split is now supported by fresh live evidence instead of partial/stale surfaces.
