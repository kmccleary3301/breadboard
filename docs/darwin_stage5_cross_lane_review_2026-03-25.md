# DARWIN Stage-5 Cross-Lane Review

Date: 2026-03-25
Status: reviewed
References:
- `docs/darwin_stage5_cross_lane_review_status_2026-03-25.md`
- `artifacts/darwin/stage5/cross_lane_review/cross_lane_review_v0.json`

## Review questions

### 1. Which lane should currently carry the main Stage-5 proving weight?

`lane.systems`.

Under the current bounded Stage-5 surface, Systems is the cleaner proving lane:

- `mixed_positive`
- `7` reuse-lift vs `5` no-lift
- no current family-surface integrity caveat in the cross-lane review bundle

### 2. Does Repo_SWE still belong in the active Stage-5 proving set?

Yes, but as a bounded challenge lane.

Repo_SWE remains important because it still exposes protocol stress and family-selection ambiguity that the cleaner Systems lane does not. That makes it useful as a challenge surface, not as the current primary proving center.

### 3. Is the Repo_SWE family-selection question resolved?

Yes, within the current bounded surface.

The current Repo_SWE family A/B artifact is:

- `completion_status=complete`
- `family_selection_status=settled_topology`
- `preferred_family_kind=topology`

That does not make Repo_SWE the stronger lane. It does make the challenge surface fresh enough to interpret honestly.

### 4. What is the next bounded Stage-5 question?

Whether the now-clean Systems-primary / Repo_SWE-challenge split is enough to pass the family-aware proving review and authorize scaled compounding planning.

## Review conclusion

The correct next move is the Stage-5 family-aware proving review and gate.

The proving split is now honest and current:

- Systems remains the cleaner lane
- Repo_SWE remains the weaker but now fresh challenge lane

That is enough to finish Tranche 2 without widening into transfer or composition yet.
