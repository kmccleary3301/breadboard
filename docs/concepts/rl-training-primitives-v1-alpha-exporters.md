RL Training Primitives V1 Phase 3 adds trainer-neutral alpha exporters on top of the graph-native RL truth surface.

The center of this tranche is `DatasetExportUnit`.

It is intentionally generic and provenance-heavy:

- `rollout_descriptor`
- `environment_descriptor`
- `policy_provenance`
- `evaluation_annotations`
- `compaction_manifests`
- export-specific payload at the edge

The exporters remain reference exporters, not trainer integrations:

- `sft_distillation`
- `rl_transition_segment`
- `verifier_example`

Each exporter flattens only at the edge. The native in-memory truth remains the `TrajectoryGraph`.

This tranche also keeps replay parity explicit. Live-derived and replay-derived exports must match at the export-core level:

- same recipe and source reference
- same policy provenance set
- same evaluation annotations
- same compaction manifests
- same export payload

The only allowed differences are path-specific metadata that should not affect downstream learning semantics.

This keeps the boundary discipline intact:

- no trainer-specific optimizer or rollout state in BreadBoard truth
- no export-specific redefinition of runtime semantics
- no transcript-order fallback as native RL truth
