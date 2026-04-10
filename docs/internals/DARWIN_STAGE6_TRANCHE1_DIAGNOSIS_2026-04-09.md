# DARWIN Stage 6 Tranche 1 diagnosis

Date: `2026-04-09`

Pre-adjustment diagnosis:

- the live Stage-6 canary was technically clean but substantively flat
- provider segmentation was already canonical on live claim-bearing rows
- both activation families stayed `flat` under the Stage-6 `single_family_lockout` comparison
- both transfer cases stayed `descriptive_only`

Why the canary stayed flat:

- `single_family_lockout` was only changing `family_context`
- it was not yet changing the execution-family envelope for the Systems-primary active family
- on the pre-adjustment canary, the Systems `warm_start` and `single_family_lockout` rows both ran with:
  - `operator_id = mut.policy.shadow_memory_enable_v1`
  - `topology_id = policy.topology.pev_v0`
  - `policy_bundle_id = policy.topology.pev_v0`
- that meant the claimed lockout was still too metadata-heavy to serve as a meaningful activation control

Bounded adjustment chosen:

- keep the lane matrix unchanged:
  - `lane.systems` primary
  - `lane.repo_swe` challenge
  - `lane.scheduling` bounded transfer target
- keep broader transfer and composition closed
- change only the Systems-primary `single_family_lockout` execution-family envelope so that it falls back to the control topology/policy envelope:
  - `topology_id = policy.topology.single_v0`
  - `policy_bundle_id = policy.topology.single_v0`
- preserve the Stage-6 comparison pairing and typed activation metadata

Why this is the smallest defensible adjustment:

- it makes `single_family_lockout` operational rather than purely descriptive
- it does not widen lanes, families, or runtime truth
- it keeps the canary comparable against the existing live Stage-6 surface
