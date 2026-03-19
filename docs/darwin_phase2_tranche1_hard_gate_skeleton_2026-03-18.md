# DARWIN Phase-2 Tranche-1 Hard-Gate Skeleton

Date: 2026-03-18
Status: draft skeleton
Scope: tranche-1 gate preparation only

## 1. Artifact completeness

- [ ] `effective_config`
- [ ] `execution_plan`
- [ ] `effective_policy`
- [ ] `evaluator_pack`
- [ ] `component_ref`
- [ ] `decision_record`
- [ ] `EvolutionLedger`

## 2. Proving coverage

- [ ] `lane.harness`
- [ ] `lane.repo_swe`
- [ ] secondary confirmation on `lane.scheduling`
- [ ] audit-only shape review on `lane.atp`

## 3. Reconstruction coverage

- [ ] scheduling promotion history reconstructed
- [ ] harness → research transfer reconstructed
- [ ] representative repo_swe search case reconstructed

## 4. Truth-boundary checks

- [ ] runtime truth unchanged
- [ ] archive remains derived-only
- [ ] no new BreadBoard kernel-truth primitive
- [ ] no runtime consumption of tranche-1 compiled artifacts

## 5. Replay / parity notes

- [ ] replay references carried where relevant
- [ ] no claim-bearing runtime regressions observed
- [ ] proving-lane emitted artifacts remain consistent with observed baseline behavior

## 6. Open semantic risks

- [ ] identify any baggy component boundaries
- [ ] identify any decision basis that is still too prose-heavy
- [ ] identify any lane-specific exceptions that should be rejected before dual-write work

## 7. Explicit tranche decision

- [ ] no-go on runtime dependency expansion for now
- [ ] next candidate move is still additive / reconstructed-only
