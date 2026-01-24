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

export interface SessionFileInfo {
  readonly path: string
  readonly type: "file" | "directory"
  readonly size?: number
  readonly updated_at?: string
}

export interface SessionFileContent {
  readonly path: string
  readonly content: string
  readonly truncated?: boolean
  readonly total_bytes?: number
}

export interface HealthResponse {
  readonly status: string
  readonly protocol_version?: string | null
  readonly version?: string | null
  readonly engine_version?: string | null
}

export interface EngineStatusResponse {
  readonly status: string
  readonly pid?: number | null
  readonly uptime_s?: number | null
  readonly protocol_version?: string | null
  readonly version?: string | null
  readonly engine_version?: string | null
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

export interface ModelCatalogEntry {
  readonly id: string
  readonly adapter?: string | null
  readonly provider?: string | null
  readonly name?: string | null
  readonly context_length?: number | null
  readonly params?: Record<string, unknown> | null
  readonly routing?: Record<string, unknown> | null
  readonly metadata?: Record<string, unknown> | null
}

export interface ModelCatalogResponse {
  readonly models: ModelCatalogEntry[]
  readonly default_model?: string | null
  readonly config_path?: string | null
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

export interface SkillCatalogResponse {
  readonly catalog: SkillCatalog
  readonly selection?: SkillSelection | null
  readonly sources?: Record<string, unknown> | null
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

export interface CTreeSnapshotResponse {
  readonly snapshot?: CTreeSnapshotSummary | Record<string, unknown> | null
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

export interface SessionCreateRequest {
  readonly config_path: string
  readonly task: string
  readonly overrides?: Record<string, unknown>
  readonly metadata?: Record<string, unknown>
  readonly workspace?: string
  readonly max_steps?: number
  readonly permission_mode?: string
  readonly stream?: boolean
}

export interface SessionCreateResponse {
  readonly session_id: string
  readonly status: string
  readonly created_at: string
  readonly logging_dir?: string | null
}

export interface SessionSummary {
  readonly session_id: string
  readonly status: string
  readonly created_at: string
  readonly last_activity_at: string
  readonly model?: string | null
  readonly mode?: string | null
  readonly completion_summary?: Record<string, unknown> | null
  readonly reward_summary?: Record<string, unknown> | null
  readonly logging_dir?: string | null
  readonly metadata?: Record<string, unknown> | null
}

export interface ErrorResponse {
  readonly message: string
  readonly detail?: Record<string, unknown>
}

export interface ApiError extends Error {
  readonly status: number
  readonly body?: unknown
}
