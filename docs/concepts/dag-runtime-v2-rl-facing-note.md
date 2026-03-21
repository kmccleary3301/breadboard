# DAG Runtime V2 RL-Facing Note

DAG Runtime V2 is now a better research substrate for RL-adjacent work, but it is
not an RL framework.

## What V2 now provides

- explicit candidate/event/frontier lineage
- grounded `SearchAssessment` truth
- barriered prune/select/terminate decisions
- branch-local state
- trajectory export with assessment linkage
- bounded reward-signal export

That is enough to support:

- offline trajectory analysis
- operator-conditioned datasets
- reward-model or evaluator studies on exported traces
- future environment wrappers outside the DAG kernel

## What remains deferred

V2 does **not** add:

- training loops
- policy optimization APIs
- online RL control surfaces
- public learner/actor abstractions

Those remain downstream research concerns. The DAG runtime stays focused on
producing interpretable search truth that future RL-facing work can consume.
