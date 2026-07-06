# Replay Admission

M4 validates deterministic replay/projection primitives on toy evidence.

Key rules:

| Rule | Effect |
| --- | --- |
| Replay mismatch | Blocks export admission. |
| Quarantine status not clear | Blocks export admission. |
| Token records invalid | Blocks export admission. |
| M4 trainability | Always false; later gates are required. |

Projection manifests record preserved and lost fields while keeping BreadBoard graph/replay/runtime as canonical truth.
