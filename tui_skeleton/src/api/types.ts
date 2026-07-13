import type {
  CTreeSnapshotResponse as GeneratedCTreeSnapshotResponse,
  E4CatalogBinding as GeneratedE4CatalogBinding,
  E4CatalogBindingSegment as GeneratedE4CatalogBindingSegment,
  E4CatalogEntry as GeneratedE4CatalogEntry,
  E4CatalogPage as GeneratedE4CatalogPage,
  E4ClaimDetail as GeneratedE4ClaimDetail,
  E4ClaimList as GeneratedE4ClaimList,
  E4CoverageMatrix as GeneratedE4CoverageMatrix,
  E4Health as GeneratedE4Health,
  E4Lane as GeneratedE4Lane,
  E4LaneDetail as GeneratedE4LaneDetail,
  E4LaneList as GeneratedE4LaneList,
  E4LedgerRow as GeneratedE4LedgerRow,
  E4LedgerRows as GeneratedE4LedgerRows,
  E4RecordEnvelope as GeneratedE4RecordEnvelope,
  E4RecordList as GeneratedE4RecordList,
  E4ResolvedArtifact as GeneratedE4ResolvedArtifact,
  E4ReverifyRequest as GeneratedE4ReverifyRequest,
  E4ReverifyResult as GeneratedE4ReverifyResult,
  E4SchemaInfo as GeneratedE4SchemaInfo,
  E4SchemaList as GeneratedE4SchemaList,
  E4SupportClaim as GeneratedE4SupportClaim,
  ErrorResponse as GeneratedErrorResponse,
  ModelCatalogEntry as GeneratedModelCatalogEntry,
  ModelCatalogResponse as GeneratedModelCatalogResponse,
  ProviderAuthAttachRequest as GeneratedProviderAuthAttachRequest,
  ProviderAuthAttachResponse as GeneratedProviderAuthAttachResponse,
  ProviderAuthDetachRequest as GeneratedProviderAuthDetachRequest,
  ProviderAuthDetachResponse as GeneratedProviderAuthDetachResponse,
  ProviderAuthStatusItem as GeneratedProviderAuthStatusItem,
  ProviderAuthStatusResponse as GeneratedProviderAuthStatusResponse,
  RegistryInfo as GeneratedRegistryInfo,
  RegistryList as GeneratedRegistryList,
  SessionCommandResponse as GeneratedSessionCommandResponse,
  SessionCreateRequest as GeneratedSessionCreateRequest,
  SessionCreateResponse as GeneratedSessionCreateResponse,
  SessionFileContent as GeneratedSessionFileContent,
  SessionFileInfo as GeneratedSessionFileInfo,
  SessionInputResponse as GeneratedSessionInputResponse,
  SessionSummary as GeneratedSessionSummary,
  SkillCatalogResponse as GeneratedSkillCatalogResponse,
} from "./generated/openapi-types.js"
import type { KernelEventV2 } from "@breadboard/kernel-contracts/generated/types/bb.kernel_event.v2"

export type EventType =
  | "stream.hello"
  | "stream.gap"
  | "session.start"
  | "session.meta"
  | "run.start"
  | "run.end"
  | "user.message"
  | "user.command"
  | "assistant.message.start"
  | "assistant.message.delta"
  | "assistant.message.end"
  | "assistant.reasoning.delta"
  | "assistant.thought_summary.delta"
  | "assistant.tool_call.start"
  | "assistant.tool_call.delta"
  | "assistant.tool_call.end"
  | "tool.exec.start"
  | "tool.exec.stdout.delta"
  | "tool.exec.stderr.delta"
  | "tool.exec.end"
  | "tool.result"
  | "permission.request"
  | "permission.decision"
  | "permission.timeout"
  | "warning"
  | "interrupt"
  | "cancel.requested"
  | "cancel.acknowledged"
  | "usage.update"
  | "agent.spawn"
  | "agent.status"
  | "agent.end"
  | "turn_start"
  | "assistant_delta"
  | "assistant_message"
  | "user_message"
  | "tool_call"
  | "tool_result"
  | "permission_request"
  | "permission_response"
  | "checkpoint_list"
  | "checkpoint_restored"
  | "skills_catalog"
  | "skills_selection"
  | "ctree_node"
  | "ctree_snapshot"
  | "task_event"
  | "reward_update"
  | "completion"
  | "log_link"
  | "error"
  | "run_finished"

export interface SessionEvent<TPayload = Record<string, unknown>> {
  readonly id: string
  readonly type: EventType
  readonly session_id: string
  readonly turn: number | null
  readonly timestamp: number
  readonly timestamp_ms?: number
  readonly seq?: number
  readonly v?: number
  readonly schema_rev?: string | null
  readonly run_id?: string | null
  readonly thread_id?: string | null
  readonly turn_id?: string | number | null
  readonly span_id?: string | null
  readonly parent_span_id?: string | null
  readonly actor?: Record<string, unknown> | null
  readonly visibility?: string | null
  readonly tags?: string[] | null
  readonly payload: TPayload
}

export type CTreeSnapshotResponse = GeneratedCTreeSnapshotResponse
export type E4CatalogBinding = GeneratedE4CatalogBinding
export type E4CatalogBindingSegment = GeneratedE4CatalogBindingSegment
export type E4CatalogEntry = GeneratedE4CatalogEntry
export type E4CatalogPage = GeneratedE4CatalogPage
export type E4ClaimDetail = GeneratedE4ClaimDetail
export type E4ClaimList = GeneratedE4ClaimList
export type E4CoverageMatrix = GeneratedE4CoverageMatrix
export type E4Health = GeneratedE4Health
export type E4Lane = GeneratedE4Lane
export type E4LaneDetail = GeneratedE4LaneDetail
export type E4LaneList = GeneratedE4LaneList
export type E4LedgerRow = GeneratedE4LedgerRow
export type E4LedgerRows = GeneratedE4LedgerRows
export type E4RecordEnvelope = GeneratedE4RecordEnvelope & {
  readonly record: KernelEventV2 | Record<string, unknown>
}
export type E4RecordList = GeneratedE4RecordList
export type E4ResolvedArtifact = GeneratedE4ResolvedArtifact
export type E4ReverifyRequest = GeneratedE4ReverifyRequest
export type E4ReverifyResult = GeneratedE4ReverifyResult
export type E4SchemaInfo = GeneratedE4SchemaInfo
export type E4SchemaList = GeneratedE4SchemaList
export type E4SupportClaim = GeneratedE4SupportClaim
export type ErrorResponse = GeneratedErrorResponse
export type ModelCatalogEntry = GeneratedModelCatalogEntry
export type ModelCatalogResponse = GeneratedModelCatalogResponse
export type ProviderAuthAttachRequest = GeneratedProviderAuthAttachRequest
export type ProviderAuthAttachResponse = GeneratedProviderAuthAttachResponse
export type ProviderAuthDetachRequest = GeneratedProviderAuthDetachRequest
export type ProviderAuthDetachResponse = GeneratedProviderAuthDetachResponse
export type ProviderAuthStatusItem = GeneratedProviderAuthStatusItem
export type ProviderAuthStatusResponse = GeneratedProviderAuthStatusResponse
export type RegistryInfo = GeneratedRegistryInfo
export type RegistryList = GeneratedRegistryList
export type SessionCommandResponse = GeneratedSessionCommandResponse
export type SessionCreateRequest = GeneratedSessionCreateRequest
export type SessionCreateResponse = GeneratedSessionCreateResponse
export type SessionFileContent = GeneratedSessionFileContent
export type SessionFileInfo = GeneratedSessionFileInfo
export type SessionInputResponse = GeneratedSessionInputResponse
export type SessionSummary = GeneratedSessionSummary
export type SkillCatalogResponse = GeneratedSkillCatalogResponse

export interface HealthResponse {
  readonly status: string
  readonly protocol_version?: string | null
  readonly version?: string | null
  readonly engine_version?: string | null
  readonly started_at?: string | null
  readonly started_at_unix?: number | null
  readonly pid?: number | null
  readonly served_revision?: {
    readonly repo_root?: string | null
    readonly commit?: string | null
    readonly branch?: string | null
    readonly dirty?: boolean | null
  } | null
}

export interface EngineStatusResponse {
  readonly status: string
  readonly pid?: number | null
  readonly uptime_s?: number | null
  readonly protocol_version?: string | null
  readonly version?: string | null
  readonly engine_version?: string | null
  readonly started_at?: string | null
  readonly started_at_unix?: number | null
  readonly served_revision?: {
    readonly repo_root?: string | null
    readonly commit?: string | null
    readonly branch?: string | null
    readonly dirty?: boolean | null
  } | null
  readonly ray?: {
    readonly available?: boolean | null
    readonly initialized?: boolean | null
  } | null
  readonly eventlog?: {
    readonly enabled?: boolean | null
    readonly dir?: string | null
    readonly bootstrap?: boolean | null
    readonly replay?: boolean | null
    readonly max_mb?: string | number | null
    readonly sessions?: number | null
    readonly last_activity?: string | null
    readonly capped?: boolean | null
  } | null
  readonly session_index?: {
    readonly enabled?: boolean | null
    readonly dir?: string | null
    readonly engine?: string | null
    readonly sessions?: number | null
    readonly last_activity?: string | null
  } | null
}

export type SkillType = "prompt" | "graph"

export interface SkillEntry {
  readonly id: string
  readonly type: SkillType
  readonly version: string
  readonly label?: string | null
  readonly group?: string | null
  readonly description?: string | null
  readonly long_description?: string | null
  readonly tags?: string[] | null
  readonly defaults?: Record<string, unknown> | null
  readonly dependencies?: string[] | null
  readonly conflicts?: string[] | null
  readonly deprecated?: boolean | null
  readonly provider_constraints?: Record<string, unknown> | null
  readonly slot?: "system" | "developer" | "user" | "per_turn" | null
  readonly steps?: number | null
  readonly determinism?: string | null
  readonly enabled?: boolean | null
}

export interface SkillSelection {
  readonly mode?: "allowlist" | "blocklist"
  readonly allowlist?: string[]
  readonly blocklist?: string[]
  readonly profile?: string | null
}

export interface SkillCatalog {
  readonly catalog_version?: string
  readonly selection?: SkillSelection | null
  readonly skills?: SkillEntry[]
  readonly prompt_skills?: Array<Record<string, unknown>>
  readonly graph_skills?: Array<Record<string, unknown>>
}

export interface CTreeNode {
  readonly id: string
  readonly digest: string
  readonly kind: string
  readonly turn?: number | null
  readonly payload: unknown
}

export interface CTreeSnapshotSummary {
  readonly schema_version: string
  readonly node_count: number
  readonly event_count: number
  readonly last_id: string | null
  readonly node_hash: string | null
}

export interface CTreeNodeEventPayload {
  readonly node: CTreeNode
  readonly snapshot: CTreeSnapshotSummary
}

export interface CTreeSnapshotEventPayload {
  readonly snapshot: Record<string, unknown> | null
  readonly compiler?: Record<string, unknown> | null
  readonly collapse?: Record<string, unknown> | null
  readonly runner?: Record<string, unknown> | null
  readonly hash_summary?: Record<string, unknown> | null
  readonly last_node?: CTreeNode | Record<string, unknown> | null
}

export interface CTreeDiskArtifact {
  readonly path: string
  readonly exists: boolean
  readonly size_bytes?: number | null
  readonly sha256?: string | null
  readonly sha256_skipped?: boolean | null
}

export interface CTreeDiskArtifactsResponse {
  readonly root: string
  readonly artifacts: Record<string, CTreeDiskArtifact>
}

export interface CTreeEventsResponse {
  readonly source: string
  readonly root?: string | null
  readonly offset: number
  readonly limit?: number | null
  readonly has_more?: boolean
  readonly truncated?: boolean
  readonly header?: Record<string, unknown> | null
  readonly events: Record<string, unknown>[]
  readonly artifact?: CTreeDiskArtifact | null
}

export type CTreeTreeStage = "RAW" | "SPEC" | "HEADER" | "FROZEN"
export type CTreeTreeSource = "auto" | "disk" | "eventlog" | "memory"

export interface CTreeTreeNode {
  readonly id: string
  readonly parent_id?: string | null
  readonly kind: string
  readonly turn?: number | null
  readonly label?: string | null
  readonly meta?: Record<string, unknown>
}

export interface CTreeTreeResponse {
  readonly source: CTreeTreeSource | string
  readonly stage: CTreeTreeStage | string
  readonly root_id: string
  readonly nodes: CTreeTreeNode[]
  readonly selection?: Record<string, unknown> | null
  readonly hashes?: Record<string, unknown> | null
}

export interface SessionArtifactInfo {
  readonly name: string
  readonly path: string
  readonly size?: number
}

export interface ProviderAuthMaterialRequest {
  readonly provider_id: string
  readonly alias?: string
  readonly api_key?: string
  readonly headers?: Record<string, string>
  readonly base_url?: string
  readonly routing?: Record<string, unknown> | null
  readonly issued_at_ms?: number
  readonly expires_at_ms?: number
  readonly ttl_seconds?: number
  readonly is_subscription_plan?: boolean
}

export interface E4ApiErrorEnvelope {
  readonly error: string
  readonly detail?: string | null
  readonly path?: string | null
}

export interface SessionKernelRecordEnvelope {
  readonly schema_version: string | null
  readonly path: string
  readonly line: number
  readonly record: KernelEventV2 | Record<string, unknown>
}

export interface SessionKernelRecordList {
  readonly session_id: string
  readonly records: SessionKernelRecordEnvelope[]
  readonly offset: number
  readonly limit: number
  readonly total: number
}

export interface RlRunSubmitRequest {
  readonly run_id: string
  readonly tenant_id: string
  readonly workspace_id: string
  readonly env_package_ref: string
  readonly target_run_id: string
  readonly requested_tasks?: number
  readonly requested_gpus?: number
  readonly requested_budget_usd?: number
  readonly requested_duration_seconds?: number
  readonly metadata?: Record<string, unknown>
}

export interface RlRunStatus {
  readonly run_id: string
  readonly state: string
  readonly target_run_id: string
  readonly accepted: boolean
  readonly cancellation_state: string
  readonly reason?: string
}

export interface RlStreamEvent {
  readonly sequence: number
  readonly run_id: string
  readonly event_type: string
  readonly state: string
  readonly message: string
  readonly target_run_id: string
  readonly payload: Record<string, unknown>
}

export interface RlArtifact {
  readonly artifact_id: string
  readonly relative_path: string
  readonly sha256: string
  readonly bytes: number
  readonly egress_allowed: boolean
}

export interface RlAuditReport {
  readonly run_id: string
  readonly tenant_id: string
  readonly workspace_id: string
  readonly target_run_id: string
  readonly state: string
  readonly persistent_store: string
  readonly scorecard_update_allowed: boolean
}

export interface ApiError extends Error {
  readonly status: number
  readonly body?: unknown
}
