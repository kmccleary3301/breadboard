# DARWIN Stage-5 Replay Sufficiency Note

Date: 2026-03-25
Status: supporting note

## Replay-supported

- repo_swe topology family
- systems topology family
- retained repo_swe topology to Systems transfer is supported by source replay posture

## Replay-observed-but-weak

- systems policy family
- systems policy to Scheduling invalid transfer remains weak because Scheduling is out of current live comparison scope

## Replay-missing or not-required

- repo_swe tool-scope family remains replay-missing while held back
- family composition canary is `not_required` because composition was not authorized

## Interpretation rule

Stage-5 replay posture is sufficient for bounded internal claims and insufficient for broad transfer or composition claims.
