# RL Training Primitives V2 Freeze And Deferrals

`RL V2` closes as a proof phase, not a growth phase.

What was justified and is now part of the bounded RL helper/export layer:

- `EvaluationPackManifest`
- `ExportManifest`
- `AdapterProbeReport`

What was *not* justified:

- a policy-view witness surface
- RL-owned search contracts
- world-state delta canon
- trainer-specific packing surfaces
- study-manager ontology

The closeout decision is:

- freeze `RL V2`
- keep the V1 overlay and V2 helper surfaces
- continue future work through adapters, experiments, and downstream use unless repeated pressure clearly forces something more

This keeps BreadBoard honest:

- strong export/data claims
- bounded adapter evidence
- no unnecessary semantic ownership
