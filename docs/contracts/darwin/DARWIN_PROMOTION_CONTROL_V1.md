# DARWIN Promotion Control V1

This document freezes the first promotion-capable DARWIN control path.

## Candidate states

- `baseline`
- `retained`
- `promoted`
- `rejected`
- `superseded`

## Promotion rules

1. comparisons must share evaluator slice
2. budget class must be comparable
3. topology must be lane-compatible
4. candidate must pass verifier / checker
5. candidate must improve primary score to be promoted

If no candidate improves validly, baseline is retained.

## Rollback rule

Every promotion decision must include a rollback candidate id. In Phase-1 this is always the baseline candidate.

## Replay rule

At least one promoted candidate in the tranche must be replay-audited before internal promotion-capable claims are made.
