# DARWIN Stage-3 Transfer Comparability Note

Date: 2026-03-19
Status: recorded

## Valid retained transfer

- source: `lane.repo_swe`
- target: `lane.systems`
- budget class: preserved
- comparison class: preserved
- source replay posture: supported
- interpretation: bounded retained score-preserving transfer

## Invalid transfer

- source: `lane.repo_swe`
- target: `lane.scheduling`
- invalid reason: `unsupported_lane_scope`
- interpretation: policy-scope failure, not performance degradation
