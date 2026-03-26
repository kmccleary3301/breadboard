RL Training Primitives V1 is an overlay architecture, not a kernel rewrite.

The canonical runtime truth remains the existing BreadBoard event and DAG/search truth surfaces. RL V1 adds a trainer-neutral contract pack in `agentic_coder_prototype.rl` that projects from that truth instead of redefining it.

The first tranche settles three reconciliation points:

- `agentic_coder_prototype/search/export.py`
  - remains useful as a search-study export surface
  - is not the native RL truth surface
- `agentic_coder_prototype/optimize/trajectory_ir.py`
  - remains a shallow legacy stub
  - is superseded for RL truth by the graph-native RL overlay
- `agentic_coder_prototype/rlm`
  - remains a tool/budget subsystem
  - is not promoted into RL training truth

RL/search alignment is explicit:

- RL does not own search semantics
- RL does not create a duplicate `search_overlay` ontology
- RL projects from `SearchRun` and related DAG/search records

The initial RL contract pack introduced here is:

- `RolloutDescriptor`
- `EnvironmentDescriptor`
- `PolicyProvenance`
- `EvaluationAnnotation`
- `CostLedger`
- `CompactionManifest`
- `AdapterCapabilities`
- `TrajectoryGraph`

The native RL-facing representation is graph-shaped. The graph shell is intentionally small:

- tracks
- observations
- decisions
- effects
- causal edges

This is enough to prove the architecture is real without prematurely committing to:

- trainer-specific packing
- flat transcript truth
- scalar reward as the only evaluation channel
- RL-owned search modes

The first graph projection is built from existing DAG/search runs and keeps policy-view truth, evaluation truth, and export-facing truth separate. That separation is the architectural center of RL V1.

Phase 2 then hardens that graph path by requiring replay parity: the same source run must project to the same graph-core truth whether reconstructed live or from serialized replay payload.
