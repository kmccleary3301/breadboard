# DARWIN Contract Pack V0

Date: 2026-03-13

## Purpose

Freeze the minimal typed contract surface required to execute DARWIN Phase-1 as a domain-general evolutionary agentic platform rather than as an ATP-specialized program.

## Kernel boundary

These contracts are extension-scoped DARWIN control/evaluation contracts. They do **not** redefine BreadBoard kernel truth.

- Kernel remains generalization-first.
- DARWIN remains policy / control-plane / evaluation-plane scoped.
- Lane-specific heuristics stay outside the kernel until they have cross-lane evidence.

## Contract set

The mandatory Phase-1 contracts are:

1. `CampaignSpec`
2. `PolicyBundle`
3. `CandidateArtifact`
4. `EvaluationRecord`
5. `EvidenceBundle`
6. `ClaimRecord`

Supporting control artifacts:

7. `LaneRegistry`
8. `PolicyRegistry`
9. `WeeklyEvidencePacket`

## Phase-1 expectations

- every DARWIN claim-bearing run must reference a `CampaignSpec`
- every promoted search policy must be versioned through `PolicyBundle`
- every evolved object must normalize to `CandidateArtifact`
- every task outcome must normalize to `EvaluationRecord`
- every comparative or internal claim must point to an `EvidenceBundle`
- every approved narrative statement must normalize to `ClaimRecord`

## Promotion rule

No DARWIN primitive is eligible for BreadBoard kernel promotion unless:

- it shows value in ATP and at least two non-ATP lanes,
- semantics remain stable across two release cycles,
- rollback is explicit and cheap.
