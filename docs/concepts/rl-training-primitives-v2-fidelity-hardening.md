# RL Training Primitives V2 Fidelity Hardening

`RL V2` is a proof phase, not a kernel-expansion phase.

The `RL V1` overlay is now frozen strongly enough that the next justified work is:

- replay/live export fidelity
- compaction-aware export fidelity
- delayed-evaluation fidelity
- export-level helper manifests

What stays frozen:

- kernel event truth
- RL as an overlay
- graph-native trajectory truth
- evaluation / cost / credit separation
- flatten only at the edge
- delegated serving / evaluator / trainer / data-engine ownership

What this tranche adds:

- `EvaluationPackManifest`
- `ExportManifest`
- canonical export fingerprinting
- explicit replay/live export-manifest parity views
- explicit compaction fidelity reports
- explicit delayed-evaluation fidelity reports

What this tranche does not add:

- new DAG or search kernel nouns
- trainer-specific packing
- benchmark ontology
- RL-owned duplicate search semantics
- dataset-engine product surfaces

The intention is narrow and durable:

BreadBoard should be able to prove that the same underlying run can produce replay-stable, compaction-aware, delayed-evaluation-safe RL export truth without growing semantic ownership unnecessarily.
