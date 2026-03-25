RL Training Primitives V1 Phase 2 finishes the graph path by making both projection paths explicit:

- live-derived graph projection from `SearchRun`
- replay-derived graph projection from serialized `SearchRun` payloads

The important boundary is that both paths project into the same `TrajectoryGraph` truth surface. Replay is not allowed to invent a second graph schema, and live projection is not allowed to hide information that replay cannot reconstruct.

The parity gate is intentionally strict at the graph-core level:

- tracks
- observations
- decisions
- effects
- causal edges
- evaluation annotations
- cost ledger
- compaction manifests

The parity comparison intentionally ignores only origin-path metadata such as whether the graph was reconstructed from a live in-memory run or from replay payload. Everything else should converge.

This keeps BreadBoard aligned with the RL V1 doctrine:

- runtime truth remains canonical
- RL truth is graph-native
- replay is a first-class reconstruction path
- transcript order is not the native training truth
