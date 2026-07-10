# Kernel Schema Pack

This directory contains machine-readable schemas for the shared BreadBoard kernel contract program.

Schemas are grouped by `contracts/kernel/packs.v1.json`; every `*.schema.json` file belongs to exactly one pack.

A schema appearing here means:

- it belongs to the shared kernel contract program
- its shape is stable enough to start validating against
- its semantics should also have a paired human-readable dossier under `docs/contracts/kernel/semantics/`

A schema appearing here does **not** mean every detail of the associated semantics is complete.

## Packs

### kernel

The minimal expressive harness primitive language. This is the published BreadBoard kernel.

Status: `active`

- `bb.agent_config_surface.v1` — BreadBoard Agent Config YAML Surface V1
- `bb.agent_config_surface.v2` — BreadBoard Agent Config YAML Surface V2
- `bb.blob_ref.v1` — BreadBoard blob reference V1
- `bb.capability_registry.v1` — BreadBoard capability registry V1
- `bb.checkpoint_metadata.v1` — bb.checkpoint_metadata.v1
- `bb.config_explanation.v1` — BreadBoard config explanation V1
- `bb.config_mutation_record.v1` — BreadBoard config mutation record
- `bb.context_resource_pack.v1` — BreadBoard context resource pack
- `bb.coordination_delegated_verification_reference_slice.v1` — bb.coordination_delegated_verification_reference_slice.v1
- `bb.coordination_intervention_reference_slice.v1` — bb.coordination_intervention_reference_slice.v1
- `bb.coordination_longrun_reference_slice.v1` — bb.coordination_longrun_reference_slice.v1
- `bb.coordination_multi_worker_reference_slice.v1` — bb.coordination_multi_worker_reference_slice.v1
- `bb.coordination_pack.v3` — BreadBoard Coordination Pack V3
- `bb.coordination_reference_slice.v1` — bb.coordination_reference_slice.v1
- `bb.coordination_slice.v2` — BreadBoard Coordination Slice V2
- `bb.coordination_verification_result.v1` — bb.coordination_verification_result.v1
- `bb.directive.v1` — bb.directive.v1
- `bb.distributed_task_descriptor.v1` — bb.distributed_task_descriptor.v1
- `bb.effective_config_graph.v1` — BreadBoard effective config graph
- `bb.effective_operation_policy.v1` — BreadBoard effective operation policy V1
- `bb.effective_tool_surface.v1` — bb.effective_tool_surface.v1.schema.json
- `bb.environment_selector.v2` — BreadBoard Environment Selector V2
- `bb.execution_capability.v1` — bb.execution_capability.v1
- `bb.execution_placement.v1` — bb.execution_placement.v1
- `bb.extension_hook_execution.v1` — BreadBoard extension hook execution V1
- `bb.external_protocol_session.v1` — BreadBoard external protocol session V1
- `bb.kernel_event.v2` — BreadBoard Kernel Event V2
- `bb.kernel.common.v1` — BreadBoard kernel shared definitions V1
- `bb.memory_compaction_plan.v1` — BreadBoard memory compaction plan V1
- `bb.permission.v1` — bb.permission.v1
- `bb.projection_event.v1` — BreadBoard projection event V1
- `bb.provider_exchange.v1` — bb.provider_exchange.v1
- `bb.provider_route.v1` — BreadBoard provider route V1
- `bb.registry.v1` — BreadBoard registry V1
- `bb.replay_session.v1` — bb.replay_session.v1
- `bb.resource_access.v1` — BreadBoard resource access V1
- `bb.resource_ref.v1` — BreadBoard resource reference V1
- `bb.review_verdict.v1` — bb.review_verdict.v1
- `bb.run_context.v1` — bb.run_context.v1
- `bb.run_request.v1` — bb.run_request.v1
- `bb.sandbox_request.v1` — bb.sandbox_request.v1
- `bb.sandbox_result.v1` — bb.sandbox_result.v1
- `bb.schema_lifecycle.v1` — BreadBoard schema lifecycle registry V1
- `bb.session_transcript.v2` — BreadBoard Session Transcript V2
- `bb.side_effect_broker.v1` — BreadBoard side effect broker V1
- `bb.signal.v1` — bb.signal.v1
- `bb.task.v1` — bb.task.v1
- `bb.terminal_cleanup_result.v1` — bb.terminal_cleanup_result.v1.schema.json
- `bb.terminal_interaction.v1` — bb.terminal_interaction.v1.schema.json
- `bb.terminal_output_delta.v1` — bb.terminal_output_delta.v1.schema.json
- `bb.terminal_registry_snapshot.v1` — bb.terminal_registry_snapshot.v1.schema.json
- `bb.terminal_session_descriptor.v1` — bb.terminal_session_descriptor.v1.schema.json
- `bb.terminal_session_end.v1` — bb.terminal_session_end.v1.schema.json
- `bb.tool_binding.v1` — bb.tool_binding.v1.schema.json
- `bb.tool_call.v2` — BreadBoard Tool Call V2
- `bb.tool_execution_outcome.v2` — BreadBoard Tool Execution Outcome V2
- `bb.tool_model_render.v2` — BreadBoard Tool Model Render V2
- `bb.tool_spec.v2` — BreadBoard Tool Spec V2
- `bb.transcript_continuation_patch.v1` — bb.transcript_continuation_patch.v1
- `bb.wake_subscription.v1` — bb.wake_subscription.v1
- `bb.work_item.v1` — BreadBoard work item V1

### e4

E4 evidence-pack records: capture, claims, catalogs, lanes, coverage, comparators, closure reports.

Status: `active`

- `bb.e4.artifact_catalog.v2` — BreadBoard E4 artifact catalog V2
- `bb.e4.closure_report.v1` — BreadBoard E4 closure report V1
- `bb.e4.common.v1` — BreadBoard E4 shared definitions V1
- `bb.e4.comparator_registry.v1` — BreadBoard E4 comparator registry V1
- `bb.e4.fixed_point_report.v1` — BreadBoard E4 fixed-point report V1
- `bb.e4.lane_def.v2` — BreadBoard E4 lane definition V2
- `bb.e4.lane_inventory.v2` — BreadBoard E4 lane inventory V2
- `bb.e4.regen_failure_classification.v1` — BreadBoard E4 regeneration failure classification V1
- `bb.e4.regen_plan.v1` — BreadBoard E4 regeneration plan V1
- `bb.e4.support_claim.v4` — BreadBoard E4 C4 support claim V4
- `bb.e4.target_coverage.v2` — BreadBoard E4 target coverage matrix V2
- `bb.lane_validation_report.v1` — BreadBoard E4 lane validation report V1
- `bb.tool_support_claim.v1` — bb.tool_support_claim.v1.schema.json

### program

Program-management/campaign accounting records. Not harness primitives.

Status: `active`

- `bb.atomic_feature_ledger.v1` — BreadBoard atomic feature ledger row
- `bb.er.progress.v1` — BreadBoard ER progress tracker V1
- `bb.unsupported_case.v1` — bb.unsupported_case.v1

### legacy_frozen

Superseded for new production; retained to validate frozen accepted evidence and not-yet-migrated lanes. Producers MUST NOT emit these for new artifacts.

Status: `active`

- `bb.e4.artifact_catalog.v1` — BreadBoard E4 artifact catalog V1
- `bb.e4.lane_def.v1` — BreadBoard E4 lane definition V1
- `bb.e4.lane_inventory.v1` — BreadBoard E4 lane inventory V1
- `bb.e4.support_claim.v1` — BreadBoard E4 C4 support claim
- `bb.e4.support_claim.v2` — BreadBoard E4 C4 support claim V2
- `bb.e4.support_claim.v3` — BreadBoard E4 C4 support claim V3
- `bb.e4.target_coverage.v1` — BreadBoard E4 target coverage matrix V1
- `bb.environment_selector.v1` — bb.environment_selector.v1.schema.json
- `bb.kernel_event.v1` — BreadBoard Kernel Event V1
- `bb.session_transcript.v1` — BreadBoard Session Transcript V1
- `bb.tool_call.v1` — BreadBoard Tool Call V1
- `bb.tool_execution_outcome.v1` — BreadBoard Tool Execution Outcome V1
- `bb.tool_model_render.v1` — BreadBoard Tool Model Render V1
- `bb.tool_spec.v1` — BreadBoard Tool Spec V1
