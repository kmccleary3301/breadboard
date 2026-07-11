# Kernel Schema Pack

This directory contains machine-readable schemas for the shared BreadBoard kernel contract program.

Schemas are grouped by `contracts/kernel/packs.v1.json`; the three Phase 20 governance schemas remain registry-only so pack membership does not change.

A schema appearing here means:

- it belongs to the shared kernel contract program
- its shape is stable enough to start validating against
- its semantics should also have a paired human-readable dossier under `docs/contracts/kernel/semantics/`

A schema appearing here does **not** mean every detail of the associated semantics is complete.

Tiers are generated from `contracts/kernel/registries/contract_tiers.v1.json`. “Minimal” refers only to the `runtime_protocol` tier.

## Packs

### kernel

Published BreadBoard kernel schemas span multiple contract tiers. "Minimal" refers only to the runtime_protocol tier; see contracts/kernel/registries/contract_tiers.v1.json.

Status: `active`

| Schema | Title | Tier |
| --- | --- | --- |
| `bb.agent_config_surface.v1` | BreadBoard Agent Config YAML Surface V1 | `config_algebra` |
| `bb.agent_config_surface.v2` | BreadBoard Agent Config YAML Surface V2 | `config_algebra` |
| `bb.blob_ref.v1` | BreadBoard blob reference V1 | `host_protocol` |
| `bb.capability_registry.v1` | BreadBoard capability registry V1 | `runtime_protocol` |
| `bb.checkpoint_metadata.v1` | bb.checkpoint_metadata.v1 | `host_protocol` |
| `bb.config_explanation.v1` | BreadBoard config explanation V1 | `config_algebra` |
| `bb.config_mutation_record.v1` | BreadBoard config mutation record | `config_algebra` |
| `bb.context_resource_pack.v1` | BreadBoard context resource pack | `config_algebra` |
| `bb.coordination_delegated_verification_reference_slice.v1` | bb.coordination_delegated_verification_reference_slice.v1 | `evidence` |
| `bb.coordination_intervention_reference_slice.v1` | bb.coordination_intervention_reference_slice.v1 | `evidence` |
| `bb.coordination_longrun_reference_slice.v1` | bb.coordination_longrun_reference_slice.v1 | `evidence` |
| `bb.coordination_multi_worker_reference_slice.v1` | bb.coordination_multi_worker_reference_slice.v1 | `evidence` |
| `bb.coordination_pack.v3` | BreadBoard Coordination Pack V3 | `runtime_protocol` |
| `bb.coordination_reference_slice.v1` | bb.coordination_reference_slice.v1 | `evidence` |
| `bb.coordination_slice.v2` | BreadBoard Coordination Slice V2 | `runtime_protocol` |
| `bb.coordination_verification_result.v1` | bb.coordination_verification_result.v1 | `evidence` |
| `bb.directive.v1` | bb.directive.v1 | `host_protocol` |
| `bb.distributed_task_descriptor.v1` | bb.distributed_task_descriptor.v1 | `host_protocol` |
| `bb.effective_config_graph.v1` | BreadBoard effective config graph | `runtime_protocol` |
| `bb.effective_operation_policy.v1` | BreadBoard effective operation policy V1 | `runtime_protocol` |
| `bb.effective_tool_surface.v1` | bb.effective_tool_surface.v1.schema.json | `runtime_protocol` |
| `bb.environment_selector.v2` | BreadBoard Environment Selector V2 | `config_algebra` |
| `bb.execution_capability.v1` | bb.execution_capability.v1 | `host_protocol` |
| `bb.execution_placement.v1` | bb.execution_placement.v1 | `host_protocol` |
| `bb.extension_hook_execution.v1` | BreadBoard extension hook execution V1 | `host_protocol` |
| `bb.external_protocol_session.v1` | BreadBoard external protocol session V1 | `host_protocol` |
| `bb.kernel_event.v2` | BreadBoard Kernel Event V2 | `runtime_protocol` |
| `bb.kernel.common.v1` | BreadBoard kernel shared definitions V1 | `config_algebra` |
| `bb.memory_compaction_plan.v1` | BreadBoard memory compaction plan V1 | `host_protocol` |
| `bb.permission.v1` | bb.permission.v1 | `host_protocol` |
| `bb.projection_event.v1` | BreadBoard projection event V1 | `host_protocol` |
| `bb.provider_exchange.v1` | bb.provider_exchange.v1 | `host_protocol` |
| `bb.provider_route.v1` | BreadBoard provider route V1 | `host_protocol` |
| `bb.registry.v1` | BreadBoard registry V1 | `config_algebra` |
| `bb.replay_session.v1` | bb.replay_session.v1 | `evidence` |
| `bb.resource_access.v1` | BreadBoard resource access V1 | `host_protocol` |
| `bb.resource_ref.v1` | BreadBoard resource reference V1 | `host_protocol` |
| `bb.review_verdict.v1` | bb.review_verdict.v1 | `host_protocol` |
| `bb.run_context.v1` | bb.run_context.v1 | `host_protocol` |
| `bb.run_request.v1` | bb.run_request.v1 | `host_protocol` |
| `bb.sandbox_request.v1` | bb.sandbox_request.v1 | `host_protocol` |
| `bb.sandbox_result.v1` | bb.sandbox_result.v1 | `host_protocol` |
| `bb.schema_lifecycle.v1` | BreadBoard schema lifecycle registry V1 | `evidence` |
| `bb.session_transcript.v2` | BreadBoard Session Transcript V2 | `runtime_protocol` |
| `bb.side_effect_broker.v1` | BreadBoard side effect broker V1 | `host_protocol` |
| `bb.signal.v1` | bb.signal.v1 | `host_protocol` |
| `bb.task.v1` | bb.task.v1 | `evidence` |
| `bb.terminal_cleanup_result.v1` | bb.terminal_cleanup_result.v1.schema.json | `host_protocol` |
| `bb.terminal_interaction.v1` | bb.terminal_interaction.v1.schema.json | `host_protocol` |
| `bb.terminal_output_delta.v1` | bb.terminal_output_delta.v1.schema.json | `host_protocol` |
| `bb.terminal_registry_snapshot.v1` | bb.terminal_registry_snapshot.v1.schema.json | `host_protocol` |
| `bb.terminal_session_descriptor.v1` | bb.terminal_session_descriptor.v1.schema.json | `host_protocol` |
| `bb.terminal_session_end.v1` | bb.terminal_session_end.v1.schema.json | `host_protocol` |
| `bb.tool_binding.v1` | bb.tool_binding.v1.schema.json | `config_algebra` |
| `bb.tool_call.v2` | BreadBoard Tool Call V2 | `runtime_protocol` |
| `bb.tool_execution_outcome.v2` | BreadBoard Tool Execution Outcome V2 | `runtime_protocol` |
| `bb.tool_model_render.v2` | BreadBoard Tool Model Render V2 | `runtime_protocol` |
| `bb.tool_spec.v2` | BreadBoard Tool Spec V2 | `config_algebra` |
| `bb.transcript_continuation_patch.v1` | bb.transcript_continuation_patch.v1 | `host_protocol` |
| `bb.wake_subscription.v1` | bb.wake_subscription.v1 | `host_protocol` |
| `bb.work_item.v1` | BreadBoard work item V1 | `runtime_protocol` |

### e4

E4 evidence-pack records: capture, claims, catalogs, lanes, coverage, comparators, closure reports.

Status: `active`

| Schema | Title | Tier |
| --- | --- | --- |
| `bb.e4.artifact_catalog.v2` | BreadBoard E4 artifact catalog V2 | `evidence` |
| `bb.e4.closure_report.v1` | BreadBoard E4 closure report V1 | `evidence` |
| `bb.e4.common.v1` | BreadBoard E4 shared definitions V1 | `evidence` |
| `bb.e4.comparator_registry.v1` | BreadBoard E4 comparator registry V1 | `evidence` |
| `bb.e4.fixed_point_report.v1` | BreadBoard E4 fixed-point report V1 | `evidence` |
| `bb.e4.lane_def.v2` | BreadBoard E4 lane definition V2 | `evidence` |
| `bb.e4.lane_inventory.v2` | BreadBoard E4 lane inventory V2 | `evidence` |
| `bb.e4.regen_failure_classification.v1` | BreadBoard E4 regeneration failure classification V1 | `evidence` |
| `bb.e4.regen_plan.v1` | BreadBoard E4 regeneration plan V1 | `evidence` |
| `bb.e4.support_claim.v4` | BreadBoard E4 C4 support claim V4 | `evidence` |
| `bb.e4.target_coverage.v2` | BreadBoard E4 target coverage matrix V2 | `evidence` |
| `bb.lane_validation_report.v1` | BreadBoard E4 lane validation report V1 | `evidence` |
| `bb.tool_support_claim.v1` | bb.tool_support_claim.v1.schema.json | `evidence` |

### program

Program-management/campaign accounting records. Not harness primitives.

Status: `active`

| Schema | Title | Tier |
| --- | --- | --- |
| `bb.atomic_feature_ledger.v1` | BreadBoard atomic feature ledger row | `evidence` |
| `bb.er.progress.v1` | BreadBoard ER progress tracker V1 | `evidence` |
| `bb.unsupported_case.v1` | bb.unsupported_case.v1 | `evidence` |

### legacy_frozen

Superseded for new production; retained to validate frozen accepted evidence and not-yet-migrated lanes. Producers MUST NOT emit these for new artifacts.

Status: `active`

| Schema | Title | Tier |
| --- | --- | --- |
| `bb.e4.artifact_catalog.v1` | BreadBoard E4 artifact catalog V1 | `frozen_legacy` |
| `bb.e4.lane_def.v1` | BreadBoard E4 lane definition V1 | `frozen_legacy` |
| `bb.e4.lane_inventory.v1` | BreadBoard E4 lane inventory V1 | `frozen_legacy` |
| `bb.e4.support_claim.v1` | BreadBoard E4 C4 support claim | `frozen_legacy` |
| `bb.e4.support_claim.v2` | BreadBoard E4 C4 support claim V2 | `frozen_legacy` |
| `bb.e4.support_claim.v3` | BreadBoard E4 C4 support claim V3 | `frozen_legacy` |
| `bb.e4.target_coverage.v1` | BreadBoard E4 target coverage matrix V1 | `frozen_legacy` |
| `bb.environment_selector.v1` | bb.environment_selector.v1.schema.json | `frozen_legacy` |
| `bb.kernel_event.v1` | BreadBoard Kernel Event V1 | `frozen_legacy` |
| `bb.session_transcript.v1` | BreadBoard Session Transcript V1 | `frozen_legacy` |
| `bb.tool_call.v1` | BreadBoard Tool Call V1 | `frozen_legacy` |
| `bb.tool_execution_outcome.v1` | BreadBoard Tool Execution Outcome V1 | `frozen_legacy` |
| `bb.tool_model_render.v1` | BreadBoard Tool Model Render V1 | `frozen_legacy` |
| `bb.tool_spec.v1` | BreadBoard Tool Spec V1 | `frozen_legacy` |

### phase20_registry

Phase 20 governance schemas are classified without changing pack membership.

Status: `active`

| Schema | Title | Tier |
| --- | --- | --- |
| `bb.contract_tiers.v1` | Consumer-backed tier registry for the published contract estate | `evidence` |
| `bb.e4.lane_lock.v1` | BreadBoard E4 lane lock (machine-owned deterministic resolution; never hand-edited) | `config_algebra` |
| `bb.e4.lane_manifest.v1` | BreadBoard E4 lane manifest (author-owned conformance intent; digests live in bb.e4.lane_lock.v1) | `config_algebra` |
