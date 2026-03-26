import Ajv2020Module from "ajv/dist/2020.js"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

export interface KernelEventV1 {
  schemaVersion: "bb.kernel_event.v1"
  eventId: string
  runId: string
  sessionId: string
  seq: number
  ts: string
  actor: "engine" | "provider" | "tool" | "subagent" | "human" | "service"
  visibility: "model" | "host" | "audit"
  kind: string
  payload: unknown
  turnId?: string
  stepId?: string
  taskId?: string
  callId?: string
  causedBy?: string
}

export interface SessionTranscriptV1Item {
  kind: string
  visibility: "model" | "host" | "audit"
  callId?: string
  provenance?: Record<string, unknown>
  content: unknown
}

export interface SessionTranscriptV1 {
  schemaVersion: "bb.session_transcript.v1"
  sessionId: string
  runId?: string
  eventCursor?: number | null
  items: SessionTranscriptV1Item[]
  metadata?: Record<string, unknown>
}

export interface ToolCallV1 {
  schemaVersion: "bb.tool_call.v1"
  callId: string
  toolName: string
  args: unknown
  state: string
  runId?: string
  sessionId?: string
  turnId?: string
  stepId?: string
  taskId?: string
  metadata?: Record<string, unknown>
}

export interface ToolSpecV1 {
  schemaVersion: "bb.tool_spec.v1"
  name: string
  aliases?: string[]
  description: string
  inputSchema: Record<string, unknown>
  outputSchema?: Record<string, unknown>
  approvalPolicy?: Record<string, unknown>
  visibility?: Record<string, unknown>
  capabilityTags?: string[]
}

export interface ToolExecutionOutcomeV1 {
  schemaVersion: "bb.tool_execution_outcome.v1"
  callId: string
  terminalState: "completed" | "failed" | "cancelled" | "denied"
  result?: unknown
  error?: Record<string, unknown>
  artifacts?: Array<Record<string, unknown>>
  usage?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface ToolModelRenderV1 {
  schemaVersion: "bb.tool_model_render.v1"
  callId: string
  parts: unknown[]
  truncation?: Record<string, unknown>
  visibility?: "model" | "host" | "audit"
  metadata?: Record<string, unknown>
}

export interface RunRequestV1 {
  schema_version: "bb.run_request.v1"
  request_id: string
  entry_mode: string
  task: string
  config_ref?: string | null
  workspace_root?: string | null
  requested_model?: string | null
  requested_features?: Record<string, unknown>
  requested_execution?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface RunContextV1 {
  schema_version: "bb.run_context.v1"
  session_id: string
  engine_family: string
  request_id: string
  engine_ref?: string | null
  workspace_root?: string | null
  resolved_model?: string | null
  resolved_provider_route?: string | null
  active_mode?: string | null
  feature_flags?: Record<string, unknown>
  execution_mode?: string | null
  delegated_services?: string[]
  metadata?: Record<string, unknown>
}

export interface ProviderExchangeV1 {
  schema_version: "bb.provider_exchange.v1"
  exchange_id: string
  request: {
    provider_family: string
    runtime_id: string
    route_id?: string | null
    model: string
    stream: boolean
    message_count?: number
    tool_count?: number
    metadata?: Record<string, unknown>
  }
  response: {
    message_count: number
    finish_reasons?: Array<string | null>
    usage?: Record<string, unknown> | null
    metadata?: Record<string, unknown>
    evidence_refs?: string[]
  }
}

export interface PermissionV1 {
  schema_version: "bb.permission.v1"
  request_id: string
  category: string
  pattern: string
  scope?: string | null
  metadata?: Record<string, unknown>
  decision: string
  decision_source?: string | null
  reason?: string | null
  requires_host_interaction?: boolean
  audit_refs?: string[]
}

export interface ExecutionCapabilityV1 {
  schema_version: "bb.execution_capability.v1"
  capability_id: string
  security_tier: "trusted_dev" | "single_tenant" | "shared_host" | "multi_tenant"
  isolation_class: "none" | "process" | "oci" | "gvisor" | "kata" | "microvm" | "remote_service"
  allow_read_paths?: string[]
  allow_write_paths?: string[]
  allow_net_hosts?: string[]
  allow_run_programs?: string[]
  allow_env_keys?: string[]
  secret_mode: "inline" | "ref_only" | "scoped_proxy"
  tty_mode?: "none" | "optional" | "required"
  resource_budget?: Record<string, unknown> | null
  evidence_mode: "minimal" | "replay_strict" | "audit_full"
}

export interface ExecutionPlacementV1 {
  schema_version: "bb.execution_placement.v1"
  placement_id: string
  placement_class:
    | "inline_ts"
    | "local_process"
    | "local_oci"
    | "local_oci_gvisor"
    | "local_oci_kata"
    | "local_microvm"
    | "remote_worker"
    | "delegated_python"
    | "delegated_oci"
    | "delegated_microvm"
  runtime_id: string
  capability_id?: string | null
  satisfied_security_tier?: string | null
  downgrade_reason?: string | null
  metadata?: Record<string, unknown>
}

export interface SandboxRequestV1 {
  schema_version: "bb.sandbox_request.v1"
  request_id: string
  capability_id?: string | null
  placement_class?: string | null
  workspace_ref?: string | null
  rootfs_ref?: string | null
  image_ref?: string | null
  snapshot_ref?: string | null
  command: string[]
  network_policy?: Record<string, unknown> | null
  secret_refs?: string[]
  timeout_seconds?: number | null
  evidence_mode: "minimal" | "replay_strict" | "audit_full"
  metadata?: Record<string, unknown>
}

export interface SandboxResultV1 {
  schema_version: "bb.sandbox_result.v1"
  request_id: string
  status: "completed" | "failed" | "cancelled" | "timed_out"
  placement_id?: string | null
  stdout_ref?: string | null
  stderr_ref?: string | null
  artifact_refs?: string[]
  side_effect_digest?: string | null
  usage?: Record<string, unknown> | null
  evidence_refs?: string[]
  error?: Record<string, unknown> | null
}

export type SignalCodeV1 =
  | "partial_complete"
  | "merge_ready"
  | "complete"
  | "blocked"
  | "no_progress"
  | "retryable_failure"
  | "catastrophic_failure"
  | "human_required"

export type SignalAuthorityScopeV1 = "task" | "mission"

export type SignalStatusV1 = "proposed" | "accepted" | "rejected"

export type SignalSourceKindV1 =
  | "assistant_content"
  | "text_sentinel"
  | "provider_finish"
  | "tool_call"
  | "runtime"
  | "worker"
  | "supervisor"
  | "host"
  | "system"

export type SignalEmitterRoleV1 = "assistant" | "worker" | "supervisor" | "host" | "runtime" | "system"

export interface SignalSourceV1 {
  kind: SignalSourceKindV1
  emitter_role: SignalEmitterRoleV1
  detail?: string | null
}

export interface SignalValidationV1 {
  accepted: boolean
  reasons: string[]
  validated_by: string
  validated_at?: number | null
}

export interface SignalV1 {
  schema_version: "bb.signal.v1"
  signal_id: string
  code: SignalCodeV1
  task_id: string
  parent_task_id?: string | null
  mission_task_id?: string | null
  authority_scope: SignalAuthorityScopeV1
  status: SignalStatusV1
  source: SignalSourceV1
  evidence_refs: string[]
  payload: Record<string, unknown>
  validation?: SignalValidationV1 | null
}

export type WakeSubscriptionActionV1 = "resume"

export interface WakeSubscriptionV1 {
  schema_version: "bb.wake_subscription.v1"
  subscription_id: string
  on_codes: SignalCodeV1[]
  action: WakeSubscriptionActionV1
  from_task_ids?: string[]
  include_descendants?: boolean
  coalesce_window_ms?: number
}

export type ReviewVerdictCodeV1 =
  | "validated"
  | "pending_validation"
  | "retry"
  | "checkpoint"
  | "escalate"
  | "human_required"
  | "noted"

export type ReviewVerdictReviewerRoleV1 = "supervisor" | "host" | "system"

export interface ReviewVerdictSubjectV1 {
  kind: "signal"
  signal_id: string
  signal_event_id?: number | null
  signal_code: SignalCodeV1
  source_task_id: string
  mission_task_id?: string | null
  subscription_id?: string | null
  trigger_signal_id?: string | null
  trigger_event_id?: number | null
  trigger_code?: SignalCodeV1 | null
}

export interface ReviewVerdictValidationV1 {
  accepted: boolean
  reasons: string[]
  validated_by: string
  validated_at?: number | null
}

export interface ReviewVerdictV1 {
  schema_version: "bb.review_verdict.v1"
  verdict_id: string
  reviewer_task_id: string
  reviewer_role: ReviewVerdictReviewerRoleV1
  subject: ReviewVerdictSubjectV1
  verdict_code: ReviewVerdictCodeV1
  mission_completed: boolean
  required_deliverable_refs: string[]
  deliverable_refs: string[]
  missing_deliverable_refs: string[]
  blocking_reason?: string | null
  recommended_next_action?: "retry" | "checkpoint" | "escalate" | "human_required" | null
  support_claim_ref?: string | null
  signal_evidence_refs: string[]
  metadata: Record<string, unknown>
  validation?: ReviewVerdictValidationV1 | null
}

export type DirectiveCodeV1 = "continue" | "retry" | "checkpoint" | "escalate" | "terminate"

export type DirectiveIssuerRoleV1 = "supervisor" | "host" | "system"

export interface DirectiveValidationV1 {
  accepted: boolean
  reasons: string[]
  validated_by: string
  validated_at?: number | null
}

export interface DirectiveV1 {
  schema_version: "bb.directive.v1"
  directive_id: string
  directive_code: DirectiveCodeV1
  issuer_task_id: string
  issuer_role: DirectiveIssuerRoleV1
  target_task_id: string
  target_job_id?: string | null
  based_on_verdict_id: string
  based_on_signal_id: string
  payload: Record<string, unknown>
  evidence_refs: string[]
  metadata: Record<string, unknown>
  validation?: DirectiveValidationV1 | null
}

export interface CoordinationVerificationResultV1 {
  schema_version: "bb.coordination_verification_result.v1"
  subject_signal_id: string
  subject_task_id: string
  validator_task_id: string
  status: "pass" | "fail" | "soft_fail"
  verification_artifact_refs: string[]
  summary?: string
}

export interface CoordinationInterventionSnapshotV1 {
  intervention_id: string
  status: "pending" | "resolved"
  review_verdict_id: string
  signal_id: string
  source_task_id: string
  mission_task_id?: string | null
  required_input?: string | null
  blocking_reason?: string | null
  allowed_host_actions?: DirectiveCodeV1[]
  review_verdict: ReviewVerdictV1
  signal: SignalV1 | null
  directives: DirectiveV1[]
  host_responses: DirectiveV1[]
}

export interface CoordinationInspectionSnapshotV1 {
  signals: SignalV1[]
  review_verdicts: ReviewVerdictV1[]
  directives: DirectiveV1[]
  latest_signal_by_code: Partial<Record<SignalCodeV1, SignalV1>>
  unresolved_interventions: CoordinationInterventionSnapshotV1[]
  resolved_interventions: CoordinationInterventionSnapshotV1[]
}

export interface DistributedTaskDescriptorV1 {
  schema_version: "bb.distributed_task_descriptor.v1"
  task_id: string
  task_kind: "turn" | "step" | "subagent" | "background" | "workflow"
  parent_task_id?: string | null
  placement_preferences?: string[]
  checkpoint_strategy?: string | null
  wake_conditions?: string[]
  wake_subscriptions?: WakeSubscriptionV1[]
  join_policy?: string | null
  retry_policy?: Record<string, unknown> | null
  priority?: number | null
  budget?: Record<string, unknown> | null
  expected_output_contract?: string | null
  artifact_refs?: string[]
}

export interface TranscriptContinuationPatchV1 {
  schema_version: "bb.transcript_continuation_patch.v1"
  patch_id: string
  pre_state_ref?: string | null
  appended_messages: Array<Record<string, unknown>>
  appended_tool_events?: Array<Record<string, unknown>>
  lineage_updates?: Array<Record<string, unknown>>
  compaction_markers?: Array<Record<string, unknown>>
  post_state_digest: string
  lossiness_flags?: string[]
}

export interface TerminalSessionDescriptorV1PublicHandle {
  namespace: string
  label: string
  value: string | number
  audience: "model" | "host"
}

export interface TerminalSessionDescriptorV1 {
  schema_version: "bb.terminal_session_descriptor.v1"
  terminal_session_id: string
  startup_call_id?: string | null
  owner_task_id?: string | null
  public_handles?: TerminalSessionDescriptorV1PublicHandle[]
  command: string[]
  cwd?: string | null
  stream_mode: "pty" | "pipes"
  stream_split?: "stdout_stderr" | "merged"
  capability_id?: string | null
  placement_id?: string | null
  persistence_scope: "turn" | "thread" | "until_cleanup"
  continuation_scope: "none" | "model" | "host" | "both"
}

export interface TerminalOutputDeltaV1 {
  schema_version: "bb.terminal_output_delta.v1"
  terminal_session_id: string
  startup_call_id?: string | null
  causing_call_id?: string | null
  stream: "stdout" | "stderr" | "merged"
  chunk_b64: string
  chunk_seq: number
}

export interface TerminalInteractionV1 {
  schema_version: "bb.terminal_interaction.v1"
  terminal_session_id: string
  startup_call_id?: string | null
  causing_call_id?: string | null
  interaction_kind: "stdin" | "poll" | "signal"
  input_b64?: string | null
  signal?: string | null
}

export interface TerminalSessionEndV1 {
  schema_version: "bb.terminal_session_end.v1"
  terminal_session_id: string
  startup_call_id?: string | null
  causing_call_id?: string | null
  terminal_state: "completed" | "failed" | "cancelled" | "cleaned_up" | "backend_lost"
  exit_code?: number | null
  duration_ms?: number | null
  artifact_refs?: string[]
  evidence_refs?: string[]
}

export interface TerminalRegistrySnapshotV1 {
  schema_version: "bb.terminal_registry_snapshot.v1"
  snapshot_id: string
  active_sessions: TerminalSessionDescriptorV1[]
  ended_session_ids?: string[]
}

export interface TerminalCleanupResultV1 {
  schema_version: "bb.terminal_cleanup_result.v1"
  cleanup_id: string
  scope: "single" | "all" | "filtered"
  cleaned_session_ids: string[]
  failed_session_ids?: string[]
  metadata?: Record<string, unknown>
}

export interface EnvironmentSelectorV1 {
  profile_ids?: string[]
  features?: string[]
  provider_families?: string[]
  driver_classes?: string[]
  image_ids?: string[]
  service_ids?: string[]
}

export interface ToolBindingV1 {
  schema_version: "bb.tool_binding.v1"
  binding_id: string
  tool_id: string
  binding_kind: "host" | "sandbox" | "service" | "mcp" | "delegated" | "provider_hosted" | "synthetic_only"
  environment_selector?: EnvironmentSelectorV1
  capability_id?: string | null
  placement_id?: string | null
  fallback_binding_ids?: string[]
  policy_tags?: string[]
  evidence_mode?: string | null
}

export interface ToolSupportClaimV1 {
  schema_version: "bb.tool_support_claim.v1"
  tool_id: string
  binding_id?: string | null
  level: "supported" | "degraded" | "install_required" | "unsupported" | "hidden"
  summary: string
  fallback_available?: boolean
  hidden_reason?: string | null
  exposed_to_model?: boolean
}

export interface EffectiveToolSurfaceV1 {
  schema_version: "bb.effective_tool_surface.v1"
  surface_id: string
  tool_ids: string[]
  binding_ids: string[]
  hidden_tool_ids?: string[]
  projection_profile_id?: string | null
  surface_hash?: string | null
}

export interface UnsupportedCaseV1 {
  schema_version: "bb.unsupported_case.v1"
  reason_code: string
  summary: string
  contract_family?: string | null
  fallback_allowed: boolean
  fallback_taken: boolean
  required_capability_id?: string | null
  unavailable_placement?: string | null
  evidence_refs?: string[]
  metadata?: Record<string, unknown>
}

export interface ReplaySessionV1 {
  schema_version: "bb.replay_session.v1"
  scenario_id: string
  lane_id: string
  comparator_class: string
  messages?: Array<Record<string, unknown>>
  tool_results?: Array<Record<string, unknown>>
  completion_summary?: Record<string, unknown> | null
  evidence_refs?: string[]
  strictness?: string | null
  notes?: string | null
}

export interface TaskV1 {
  schema_version: "bb.task.v1"
  task_id: string
  kind: string
  status: string
  parent_task_id?: string | null
  session_id?: string | null
  task_type?: string | null
  depth?: number | null
  description?: string | null
  visibility?: string | null
  metadata?: Record<string, unknown>
}

export interface CheckpointMetadataV1 {
  schema_version: "bb.checkpoint_metadata.v1"
  source_kind: string
  checkpoint_ref: string
  created_at: number
  path?: string
  episode?: number
  phase?: string
  updated_at?: number
  summary: Record<string, unknown>
}

export interface EngineConformanceManifestV1Row {
  engineFamily: string
  engineRef: string
  scenarioId: string
  supportTier: "draft-shape" | "draft-semantic" | "reference-engine"
  comparatorClass:
    | "shape-equal"
    | "normalized-trace-equal"
    | "model-visible-equal"
    | "workspace-side-effects-equal"
    | "projection-equal"
  evidence: string[]
  exemptions?: string[]
  notes?: string
}

export interface EngineConformanceManifestV1 {
  schemaVersion: "bb.engine_conformance_manifest.v1"
  contractVersion: string
  generatedAt?: string
  rows: EngineConformanceManifestV1Row[]
}

const MODULE_DIR = dirname(fileURLToPath(import.meta.url))

function loadTrackedSchema(name: string): unknown {
  const candidates = [
    join(MODULE_DIR, "../../../contracts/kernel/schemas", name),
    join(MODULE_DIR, "../../../../contracts/kernel/schemas", name),
  ]
  for (const candidate of candidates) {
    try {
      return JSON.parse(readFileSync(candidate, "utf8"))
    } catch {
      // Try the next candidate.
    }
  }
  throw new Error(`Unable to load tracked kernel schema: ${name}`)
}

const kernelEventSchema = loadTrackedSchema("bb.kernel_event.v1.schema.json")
const sessionTranscriptSchema = loadTrackedSchema("bb.session_transcript.v1.schema.json")
const toolCallSchema = loadTrackedSchema("bb.tool_call.v1.schema.json")
const toolSpecSchema = loadTrackedSchema("bb.tool_spec.v1.schema.json")
const toolExecutionOutcomeSchema = loadTrackedSchema("bb.tool_execution_outcome.v1.schema.json")
const toolModelRenderSchema = loadTrackedSchema("bb.tool_model_render.v1.schema.json")
const runRequestSchema = loadTrackedSchema("bb.run_request.v1.schema.json")
const runContextSchema = loadTrackedSchema("bb.run_context.v1.schema.json")
const providerExchangeSchema = loadTrackedSchema("bb.provider_exchange.v1.schema.json")
const permissionSchema = loadTrackedSchema("bb.permission.v1.schema.json")
const executionCapabilitySchema = loadTrackedSchema("bb.execution_capability.v1.schema.json")
const executionPlacementSchema = loadTrackedSchema("bb.execution_placement.v1.schema.json")
const sandboxRequestSchema = loadTrackedSchema("bb.sandbox_request.v1.schema.json")
const sandboxResultSchema = loadTrackedSchema("bb.sandbox_result.v1.schema.json")
const signalSchema = loadTrackedSchema("bb.signal.v1.schema.json")
const reviewVerdictSchema = loadTrackedSchema("bb.review_verdict.v1.schema.json")
const directiveSchema = loadTrackedSchema("bb.directive.v1.schema.json")
const coordinationVerificationResultSchema = loadTrackedSchema("bb.coordination_verification_result.v1.schema.json")
const wakeSubscriptionSchema = loadTrackedSchema("bb.wake_subscription.v1.schema.json")
const distributedTaskDescriptorSchema = loadTrackedSchema("bb.distributed_task_descriptor.v1.schema.json")
const transcriptContinuationPatchSchema = loadTrackedSchema("bb.transcript_continuation_patch.v1.schema.json")
const unsupportedCaseSchema = loadTrackedSchema("bb.unsupported_case.v1.schema.json")
const terminalSessionDescriptorSchema = loadTrackedSchema("bb.terminal_session_descriptor.v1.schema.json")
const terminalOutputDeltaSchema = loadTrackedSchema("bb.terminal_output_delta.v1.schema.json")
const terminalInteractionSchema = loadTrackedSchema("bb.terminal_interaction.v1.schema.json")
const terminalSessionEndSchema = loadTrackedSchema("bb.terminal_session_end.v1.schema.json")
const terminalRegistrySnapshotSchema = loadTrackedSchema("bb.terminal_registry_snapshot.v1.schema.json")
const terminalCleanupResultSchema = loadTrackedSchema("bb.terminal_cleanup_result.v1.schema.json")
const environmentSelectorSchema = loadTrackedSchema("bb.environment_selector.v1.schema.json")
const toolBindingSchema = loadTrackedSchema("bb.tool_binding.v1.schema.json")
const toolSupportClaimSchema = loadTrackedSchema("bb.tool_support_claim.v1.schema.json")
const effectiveToolSurfaceSchema = loadTrackedSchema("bb.effective_tool_surface.v1.schema.json")
const replaySessionSchema = loadTrackedSchema("bb.replay_session.v1.schema.json")
const taskSchema = loadTrackedSchema("bb.task.v1.schema.json")
const checkpointMetadataSchema = loadTrackedSchema("bb.checkpoint_metadata.v1.schema.json")
const engineConformanceManifestSchema = loadTrackedSchema("../manifests/bb.engine_conformance_manifest.v1.schema.json")

const AjvCtor: any = (Ajv2020Module as any).default ?? Ajv2020Module
const ajv = new AjvCtor({ allErrors: true })

function registerTrackedSchema(filename: string, schema: unknown): void {
  const schemaId =
    typeof schema === "object" && schema !== null && "$id" in schema && typeof (schema as { $id?: unknown }).$id === "string"
      ? ((schema as { $id: string }).$id)
      : null
  const registrationKey = schemaId ?? filename
  if (!ajv.getSchema(registrationKey)) {
    ajv.addSchema(schema, filename)
  }
}

registerTrackedSchema("bb.kernel_event.v1.schema.json", kernelEventSchema)
registerTrackedSchema("bb.session_transcript.v1.schema.json", sessionTranscriptSchema)
registerTrackedSchema("bb.tool_call.v1.schema.json", toolCallSchema)
registerTrackedSchema("bb.tool_spec.v1.schema.json", toolSpecSchema)
registerTrackedSchema("bb.tool_execution_outcome.v1.schema.json", toolExecutionOutcomeSchema)
registerTrackedSchema("bb.tool_model_render.v1.schema.json", toolModelRenderSchema)
registerTrackedSchema("bb.run_request.v1.schema.json", runRequestSchema)
registerTrackedSchema("bb.run_context.v1.schema.json", runContextSchema)
registerTrackedSchema("bb.provider_exchange.v1.schema.json", providerExchangeSchema)
registerTrackedSchema("bb.permission.v1.schema.json", permissionSchema)
registerTrackedSchema("bb.execution_capability.v1.schema.json", executionCapabilitySchema)
registerTrackedSchema("bb.execution_placement.v1.schema.json", executionPlacementSchema)
registerTrackedSchema("bb.sandbox_request.v1.schema.json", sandboxRequestSchema)
registerTrackedSchema("bb.sandbox_result.v1.schema.json", sandboxResultSchema)
registerTrackedSchema("bb.distributed_task_descriptor.v1.schema.json", distributedTaskDescriptorSchema)
registerTrackedSchema("bb.transcript_continuation_patch.v1.schema.json", transcriptContinuationPatchSchema)
registerTrackedSchema("bb.unsupported_case.v1.schema.json", unsupportedCaseSchema)
registerTrackedSchema("bb.terminal_session_descriptor.v1.schema.json", terminalSessionDescriptorSchema)
registerTrackedSchema("bb.terminal_output_delta.v1.schema.json", terminalOutputDeltaSchema)
registerTrackedSchema("bb.terminal_interaction.v1.schema.json", terminalInteractionSchema)
registerTrackedSchema("bb.terminal_session_end.v1.schema.json", terminalSessionEndSchema)
registerTrackedSchema("bb.terminal_registry_snapshot.v1.schema.json", terminalRegistrySnapshotSchema)
registerTrackedSchema("bb.terminal_cleanup_result.v1.schema.json", terminalCleanupResultSchema)
registerTrackedSchema("bb.environment_selector.v1.schema.json", environmentSelectorSchema)
registerTrackedSchema("bb.tool_binding.v1.schema.json", toolBindingSchema)
registerTrackedSchema("bb.tool_support_claim.v1.schema.json", toolSupportClaimSchema)
registerTrackedSchema("bb.effective_tool_surface.v1.schema.json", effectiveToolSurfaceSchema)
registerTrackedSchema("bb.replay_session.v1.schema.json", replaySessionSchema)
registerTrackedSchema("bb.task.v1.schema.json", taskSchema)
registerTrackedSchema("bb.checkpoint_metadata.v1.schema.json", checkpointMetadataSchema)
registerTrackedSchema("bb.engine_conformance_manifest.v1.schema.json", engineConformanceManifestSchema)

const validators = {
  kernelEvent: ajv.compile(kernelEventSchema),
  sessionTranscript: ajv.compile(sessionTranscriptSchema),
  toolCall: ajv.compile(toolCallSchema),
  toolSpec: ajv.compile(toolSpecSchema),
  toolExecutionOutcome: ajv.compile(toolExecutionOutcomeSchema),
  toolModelRender: ajv.compile(toolModelRenderSchema),
  runRequest: ajv.compile(runRequestSchema),
  runContext: ajv.compile(runContextSchema),
  providerExchange: ajv.compile(providerExchangeSchema),
  permission: ajv.compile(permissionSchema),
  executionCapability: ajv.compile(executionCapabilitySchema),
  executionPlacement: ajv.compile(executionPlacementSchema),
  sandboxRequest: ajv.compile(sandboxRequestSchema),
  sandboxResult: ajv.compile(sandboxResultSchema),
  signal: ajv.compile(signalSchema),
  reviewVerdict: ajv.compile(reviewVerdictSchema),
  directive: ajv.compile(directiveSchema),
  coordinationVerificationResult: ajv.compile(coordinationVerificationResultSchema),
  wakeSubscription: ajv.compile(wakeSubscriptionSchema),
  distributedTaskDescriptor: ajv.compile(distributedTaskDescriptorSchema),
  transcriptContinuationPatch: ajv.compile(transcriptContinuationPatchSchema),
  unsupportedCase: ajv.compile(unsupportedCaseSchema),
  terminalSessionDescriptor: ajv.compile(terminalSessionDescriptorSchema),
  terminalOutputDelta: ajv.compile(terminalOutputDeltaSchema),
  terminalInteraction: ajv.compile(terminalInteractionSchema),
  terminalSessionEnd: ajv.compile(terminalSessionEndSchema),
  terminalRegistrySnapshot: ajv.compile(terminalRegistrySnapshotSchema),
  terminalCleanupResult: ajv.compile(terminalCleanupResultSchema),
  environmentSelector: ajv.compile(environmentSelectorSchema),
  toolBinding: ajv.compile(toolBindingSchema),
  toolSupportClaim: ajv.compile(toolSupportClaimSchema),
  effectiveToolSurface: ajv.compile(effectiveToolSurfaceSchema),
  replaySession: ajv.compile(replaySessionSchema),
  task: ajv.compile(taskSchema),
  checkpointMetadata: ajv.compile(checkpointMetadataSchema),
  engineConformanceManifest: ajv.compile(engineConformanceManifestSchema),
}

export const kernelSchemas = {
  kernelEvent: kernelEventSchema,
  sessionTranscript: sessionTranscriptSchema,
  toolCall: toolCallSchema,
  toolSpec: toolSpecSchema,
  toolExecutionOutcome: toolExecutionOutcomeSchema,
  toolModelRender: toolModelRenderSchema,
  runRequest: runRequestSchema,
  runContext: runContextSchema,
  providerExchange: providerExchangeSchema,
  permission: permissionSchema,
  executionCapability: executionCapabilitySchema,
  executionPlacement: executionPlacementSchema,
  sandboxRequest: sandboxRequestSchema,
  sandboxResult: sandboxResultSchema,
  signal: signalSchema,
  reviewVerdict: reviewVerdictSchema,
  directive: directiveSchema,
  coordinationVerificationResult: coordinationVerificationResultSchema,
  wakeSubscription: wakeSubscriptionSchema,
  distributedTaskDescriptor: distributedTaskDescriptorSchema,
  transcriptContinuationPatch: transcriptContinuationPatchSchema,
  unsupportedCase: unsupportedCaseSchema,
  terminalSessionDescriptor: terminalSessionDescriptorSchema,
  terminalOutputDelta: terminalOutputDeltaSchema,
  terminalInteraction: terminalInteractionSchema,
  terminalSessionEnd: terminalSessionEndSchema,
  terminalRegistrySnapshot: terminalRegistrySnapshotSchema,
  terminalCleanupResult: terminalCleanupResultSchema,
  environmentSelector: environmentSelectorSchema,
  toolBinding: toolBindingSchema,
  toolSupportClaim: toolSupportClaimSchema,
  effectiveToolSurface: effectiveToolSurfaceSchema,
  replaySession: replaySessionSchema,
  task: taskSchema,
  checkpointMetadata: checkpointMetadataSchema,
  engineConformanceManifest: engineConformanceManifestSchema,
} as const

export const kernelValidators = validators

export function assertValid<T>(name: keyof typeof validators, value: unknown): T {
  const validate = validators[name]
  if (!validate(value)) {
    const text = (validate.errors || []).map((err: { instancePath?: string; message?: string }) => `${err.instancePath || "/"}: ${err.message}`).join("; ")
    throw new Error(`Kernel contract validation failed for ${name}: ${text}`)
  }
  return value as T
}
