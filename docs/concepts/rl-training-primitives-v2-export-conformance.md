# RL Training Primitives V2 Export Conformance

`RL V2 Phase 2` hardens the RL overlay at the export/conformance boundary rather than adding new semantic truth.

This tranche adds:

- canonical reference export bundles
- export conformance views
- export conformance packets
- parity views for replay/live comparison
- explicit split, contamination, transform, and fidelity-tier reporting

The key discipline is unchanged:

- BreadBoard owns semantic truth and export contracts
- BreadBoard does not absorb trainer packing, optimizer state, or dataset-engine internals
- replay-derived export remains the audit path
- graph-native truth is only flattened at the edge

The conformance layer exists so BreadBoard can make bounded claims like:

- this export family is replay-parity-verified
- this split policy and contamination guard set were used
- this adapter or downstream pipeline saw this exact export contract

without promoting trainer-specific or study-manager semantics into the platform.
