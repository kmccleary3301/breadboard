# RL Training Primitives V2 Adapter Probes

`RL V2 Phase 3` hardens support claims without turning BreadBoard into an adapter catalog product.

This tranche adds:

- `AdapterProbeReport`
- bounded probe evidence for:
  - serving / inference provenance
  - evaluator / verifier attachment
  - dataset pipeline handoff
  - trainer feedback handoff

The intent is narrow:

- `AdapterCapabilities` says what an adapter claims
- `AdapterProbeReport` says what the probe actually passed

This is still helper/conformance layer work. It does not:

- move serving, trainer, evaluator, or rollout internals into BreadBoard truth
- create a broad support-matrix ontology
- imply “supported” beyond the explicit workload family and loss map

The key discipline is that support claims remain:

- bounded
- evidence-backed
- workload-family-specific
- explicit about fidelity losses and unsupported fields
