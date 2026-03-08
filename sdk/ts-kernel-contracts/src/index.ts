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

export interface ToolExecutionOutcomeV1 {
  schemaVersion: "bb.tool_execution_outcome.v1"
  callId: string
  ok: boolean
  output?: unknown
  error?: unknown
  metadata?: Record<string, unknown>
}

export interface ToolModelRenderV1 {
  schemaVersion: "bb.tool_model_render.v1"
  callId: string
  renderKind: string
  content: unknown
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
  summary: Record<string, unknown>
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
const toolExecutionOutcomeSchema = loadTrackedSchema("bb.tool_execution_outcome.v1.schema.json")
const toolModelRenderSchema = loadTrackedSchema("bb.tool_model_render.v1.schema.json")
const runRequestSchema = loadTrackedSchema("bb.run_request.v1.schema.json")
const runContextSchema = loadTrackedSchema("bb.run_context.v1.schema.json")
const providerExchangeSchema = loadTrackedSchema("bb.provider_exchange.v1.schema.json")
const permissionSchema = loadTrackedSchema("bb.permission.v1.schema.json")
const replaySessionSchema = loadTrackedSchema("bb.replay_session.v1.schema.json")
const taskSchema = loadTrackedSchema("bb.task.v1.schema.json")
const checkpointMetadataSchema = loadTrackedSchema("bb.checkpoint_metadata.v1.schema.json")

const AjvCtor: any = (Ajv2020Module as any).default ?? Ajv2020Module
const ajv = new AjvCtor({ allErrors: true })

const validators = {
  kernelEvent: ajv.compile(kernelEventSchema),
  sessionTranscript: ajv.compile(sessionTranscriptSchema),
  toolCall: ajv.compile(toolCallSchema),
  toolExecutionOutcome: ajv.compile(toolExecutionOutcomeSchema),
  toolModelRender: ajv.compile(toolModelRenderSchema),
  runRequest: ajv.compile(runRequestSchema),
  runContext: ajv.compile(runContextSchema),
  providerExchange: ajv.compile(providerExchangeSchema),
  permission: ajv.compile(permissionSchema),
  replaySession: ajv.compile(replaySessionSchema),
  task: ajv.compile(taskSchema),
  checkpointMetadata: ajv.compile(checkpointMetadataSchema),
}

export const kernelSchemas = {
  kernelEvent: kernelEventSchema,
  sessionTranscript: sessionTranscriptSchema,
  toolCall: toolCallSchema,
  toolExecutionOutcome: toolExecutionOutcomeSchema,
  toolModelRender: toolModelRenderSchema,
  runRequest: runRequestSchema,
  runContext: runContextSchema,
  providerExchange: providerExchangeSchema,
  permission: permissionSchema,
  replaySession: replaySessionSchema,
  task: taskSchema,
  checkpointMetadata: checkpointMetadataSchema,
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
