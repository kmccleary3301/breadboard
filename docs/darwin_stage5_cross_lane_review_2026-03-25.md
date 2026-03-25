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

No.

The current derived review marks the Repo_SWE family A/B surface as `stale_or_incomplete` because the persisted round set contains mixed claim eligibility after an interrupted live rerun. That means the cross-lane decision cannot honestly rest on a claimed Repo_SWE family winner today.

### 4. What is the next bounded Stage-5 question?

Whether a systems-weighted Stage-5 compounding review remains positive when Repo_SWE is held as a bounded challenge lane rather than as a co-equal proving lane.

## Review conclusion

The correct next move is a systems-weighted Stage-5 review slice.

That keeps the proving program moving without pretending Repo_SWE is cleaner than the current evidence supports, and it avoids widening into transfer or family composition before the cross-lane proving weight is honest.
