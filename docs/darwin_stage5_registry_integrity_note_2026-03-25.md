# DARWIN Stage-5 Registry Integrity Note

Date: 2026-03-25
Status: supporting note

## Lane roles

- `lane.systems` remains the `primary_proving_lane`
- `lane.repo_swe` remains the `challenge_lane`

## Family-state interpretation

- `component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0` is `active_proving`
- `component_family.stage4.topology.policy.topology.pev_v0.lane.repo_swe.v0` is `challenge_only`
- `component_family.stage4.topology.policy.topology.pev_v0.lane.systems.v0` is `held_back`
- `component_family.stage4.tool_scope.policy.tool_scope.add_git_diff_v1.lane.repo_swe.v0` is `held_back`

## Integrity rule

Held-back families remain useful context. They are not implied Stage-5 backlog debt, active proving center, or transferable truth.
