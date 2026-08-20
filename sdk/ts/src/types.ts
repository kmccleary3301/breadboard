export interface PublicResult {
  readonly schema_version: "bb.cli.result.v1"
  readonly ok: boolean
  readonly status: "ok" | "error"
  readonly command: ReadonlyArray<string>
  readonly record_refs: ReadonlyArray<string>
  readonly hashes: Readonly<Record<string, string>>
  readonly stage_outcomes: ReadonlyArray<Readonly<Record<string, unknown>>>
  readonly warnings: ReadonlyArray<string>
  readonly next_actions: ReadonlyArray<string>
  readonly error: Readonly<Record<string, unknown>> | null
  readonly exit_code: number
  readonly data: Readonly<Record<string, unknown>>
}

export type EventType =
  | "stream.hello" | "stream.gap" | "session.start" | "session.meta" | "run.start" | "run.end"
  | "user.message" | "user.command" | "assistant.message.start" | "assistant.message.delta"
  | "assistant.message.end" | "assistant.reasoning.delta" | "assistant.thought_summary.delta"
  | "assistant.tool_call.start" | "assistant.tool_call.delta" | "assistant.tool_call.end"
  | "tool.exec.start" | "tool.exec.stdout.delta" | "tool.exec.stderr.delta" | "tool.exec.end"
  | "tool.result" | "permission.request" | "permission.decision" | "permission.timeout"
  | "warning" | "interrupt" | "cancel.requested" | "cancel.acknowledged" | "usage.update"
  | "agent.spawn" | "agent.status" | "agent.end" | "turn_start" | "assistant_delta"
  | "assistant_message" | "user_message" | "tool_call" | "tool_result" | "permission_request"
  | "permission_response" | "checkpoint_list" | "checkpoint_restored" | "skills_catalog"
  | "skills_selection" | "ctree_node" | "ctree_snapshot" | "task_event" | "reward_update"
  | "completion" | "log_link" | "error" | "run_finished"
  | "conversation.compaction.start" | "conversation.compaction.end"

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

export interface AttachmentHandle {
  readonly filename: string
  readonly id: string
  readonly mime?: string | null
  readonly size_bytes: number
}

export interface AttachmentUploadResponse {
  readonly attachments: ReadonlyArray<AttachmentHandle>
}

export interface AttachmentUploadPayload {
  readonly mime: string
  readonly base64: string
  readonly size: number
  readonly filename?: string
}

export interface SessionFileInfo {
  readonly path: string
  readonly type: "file" | "directory"
  readonly size?: number | null
  readonly updated_at?: string | null
}

export interface SessionFileContent {
  readonly path: string
  readonly content: string
  readonly truncated?: boolean
  readonly total_bytes?: number | null
}

export interface ServedRevision { readonly repo_root?: string | null; readonly commit?: string | null; readonly branch?: string | null; readonly dirty?: boolean | null }
export interface EngineRay { readonly available?: boolean | null; readonly initialized?: boolean | null }
export interface EngineEventLog { readonly enabled?: boolean | null; readonly dir?: string | null; readonly bootstrap?: boolean | null; readonly replay?: boolean | null; readonly max_mb?: string | number | null; readonly sessions?: number | null; readonly last_activity?: string | null; readonly capped?: boolean | null }
export interface HealthResponse { readonly status: string; readonly protocol_version?: string | null; readonly version?: string | null; readonly engine_version?: string | null; readonly started_at?: string | null; readonly started_at_unix?: number | null; readonly pid?: number | null; readonly served_revision?: ServedRevision | null }
export interface EngineStatusResponse { readonly status: string; readonly pid?: number | null; readonly uptime_s?: number | null; readonly protocol_version?: string | null; readonly version?: string | null; readonly engine_version?: string | null; readonly started_at?: string | null; readonly started_at_unix?: number | null; readonly served_revision?: ServedRevision | null; readonly ray?: EngineRay | null; readonly eventlog?: EngineEventLog | null; readonly session_index?: Record<string, unknown> | null }

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

export interface CTreeSnapshotResponse {
  readonly snapshot?: Record<string, unknown> | null
  readonly compiler?: Record<string, unknown> | null
  readonly collapse?: Record<string, unknown> | null
  readonly runner?: Record<string, unknown> | null
  readonly last_node?: Record<string, unknown> | null
}

export interface CTreeTreeNode {
  readonly id: string
  readonly parent_id?: string | null
  readonly kind: string
  readonly turn?: number | null
  readonly label?: string | null
  readonly meta?: Record<string, unknown>
}

export type CTreeTreeStage = "RAW" | "SPEC" | "HEADER" | "FROZEN"
export type CTreeTreeSource = "auto" | "disk" | "eventlog" | "memory"

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

export interface SessionCreateRequest {
  readonly config_path: string
  readonly task: string
  readonly overrides?: Record<string, unknown> | null
  readonly metadata?: Record<string, unknown> | null
  readonly workspace?: string | null
  readonly max_steps?: number | null
  readonly permission_mode?: string | null
  readonly stream?: boolean
}

export type SessionStatus = "starting" | "running" | "completed" | "failed" | "stopped"

export interface SessionCreateResponse {
  readonly session_id: string
  readonly status: SessionStatus | string
  readonly created_at: string
  readonly logging_dir?: string | null
}

export interface SessionSummary {
  readonly session_id: string
  readonly status: SessionStatus | string
  readonly created_at: string
  readonly last_activity_at: string
  readonly model?: string | null
  readonly mode?: string | null
  readonly completion_summary?: Record<string, unknown> | null
  readonly reward_summary?: Record<string, unknown> | null
  readonly logging_dir?: string | null
  readonly metadata?: Record<string, unknown> | null
}

export interface SessionInputRequest {
  readonly content: string
  readonly attachments?: ReadonlyArray<string> | null
}

export interface SessionInputResponse { readonly status?: string }
export interface SessionCommandRequest { readonly command: string; readonly payload?: Record<string, unknown> | null }
export interface SessionCommandResponse { readonly status?: string; readonly detail?: Record<string, unknown> | null }
export interface ErrorResponse { readonly error: string; readonly detail?: string | Record<string, unknown> | null; readonly path?: string | null }
export interface E4ApiErrorEnvelope { readonly error: string; readonly detail?: string | null; readonly path?: string | null }

export interface ReadSessionFileOptions {
  readonly mode?: "cat" | "snippet"
  readonly headLines?: number
  readonly tailLines?: number
  readonly maxBytes?: number
}

export interface SessionKernelRecordEnvelope {
  readonly schema_version: string | null
  readonly path: string
  readonly line: number
  readonly record: Record<string, unknown>
}

export interface SessionKernelRecordList {
  readonly session_id: string
  readonly records: SessionKernelRecordEnvelope[]
  readonly offset: number
  readonly limit: number
  readonly total: number
}

export interface ProviderAuthMaterial {
  readonly provider_id: string
  readonly alias?: string | null
  readonly api_key?: string | null
  readonly headers?: Record<string, unknown>
  readonly base_url?: string | null
  readonly routing?: Record<string, unknown> | null
  readonly issued_at_ms?: number | null
  readonly expires_at_ms?: number | null
  readonly ttl_seconds?: number | null
  readonly is_subscription_plan?: boolean
}
export interface ProviderAuthAttachRequest { readonly material: ProviderAuthMaterial; readonly config_path?: string | null; readonly overrides?: Record<string, unknown> | null; readonly required_profile?: Record<string, unknown> | null }
export interface ProviderAuthAttachResponse { readonly ok?: boolean; readonly detail?: Record<string, unknown> | null }
export interface ProviderAuthDetachRequest { readonly provider_id: string; readonly alias?: string | null }
export interface ProviderAuthDetachResponse { readonly ok?: boolean }
export interface ProviderAuthStatusItem { readonly provider_id: string; readonly alias?: string | null; readonly base_url?: string | null; readonly expires_at_ms?: number | null; readonly expires_in_ms?: number | null; readonly has_api_key?: boolean; readonly header_keys?: ReadonlyArray<string>; readonly is_subscription_plan?: boolean; readonly issued_at_ms?: number | null; readonly required_profile?: Record<string, unknown> | null; readonly routing_keys?: ReadonlyArray<string> }
export interface ProviderAuthStatusResponse { readonly attached?: ReadonlyArray<ProviderAuthStatusItem> }

export interface E4CatalogBindingSegment { readonly segment_id: string; readonly stable_entries_hash: string }
export interface E4CatalogBinding { readonly catalog_path: string; readonly generated_at_utc?: string | null; readonly schema_version: string; readonly segments: ReadonlyArray<E4CatalogBindingSegment>; readonly segments_hash: string; readonly stable_entries_hash: string }
export interface E4CatalogEntry { readonly role_id: string; readonly artifact_kind?: string | null; readonly bytes?: number | null; readonly exists?: boolean | null; readonly lane_id?: string | null; readonly path?: string | null; readonly sha256?: string | null; readonly [key: string]: unknown }
export interface E4CatalogPage { readonly entries: ReadonlyArray<E4CatalogEntry>; readonly revision: number; readonly total: number }
export interface E4SupportClaim { readonly accepted: boolean; readonly claim_id: string; readonly config_id?: string | null; readonly evidence_manifest_ref?: string | null; readonly kind?: string | null; readonly lane_id?: string | null; readonly schema_version?: string | null; readonly scope?: Record<string, unknown> | null; readonly summary?: string | null; readonly [key: string]: unknown }
export interface E4ClaimDetail { readonly claim: E4SupportClaim; readonly evidence_manifest: Record<string, unknown> }
export interface E4ClaimList { readonly claims: ReadonlyArray<E4SupportClaim>; readonly total: number }
export interface E4CoverageMatrix { readonly [key: string]: unknown }
export interface E4Health { readonly catalog_revision: number; readonly inventory_revision: number; readonly ok: boolean; readonly repo_root: string }
export interface E4Lane { readonly lane_id: string; readonly artifact_roles?: Record<string, unknown>; readonly builder_script?: string | null; readonly kind?: string | null; readonly phase?: string | null; readonly points?: number | null; readonly run_id?: string | null; readonly status?: string | null; readonly target_family?: string | null; readonly target_version?: string | null; readonly [key: string]: unknown }
export interface E4LaneDetail { readonly lane: E4Lane; readonly resolved_artifacts: ReadonlyArray<E4ResolvedArtifact> }
export interface E4LaneList { readonly lanes: ReadonlyArray<E4Lane>; readonly total: number }
export interface E4LedgerRow { readonly feature_id: string; readonly lane_id?: string | null; readonly points?: number | null; readonly status?: string | null; readonly [key: string]: unknown }
export interface E4LedgerRows { readonly rows: ReadonlyArray<E4LedgerRow>; readonly total: number }
export interface E4RecordEnvelope { readonly path: string; readonly record: Record<string, unknown>; readonly schema_version: string; readonly source: "evidence" | "runtime" }
export interface E4RecordList { readonly records: ReadonlyArray<E4RecordEnvelope>; readonly total: number }
export interface E4ResolvedArtifact { readonly bytes?: number | null; readonly exists: boolean; readonly path: string; readonly role: string; readonly role_id: string; readonly sha256?: string | null }
export interface E4ReverifyRequest { readonly rerun_comparators?: boolean }
export interface E4ReverifyResult { readonly checked_at: string; readonly claim_id: string; readonly comparator_rerun: boolean; readonly errors: ReadonlyArray<string>; readonly mode?: string; readonly ok: boolean }
export interface E4SchemaInfo { readonly pack: string; readonly path: string; readonly schema_id: string; readonly schema_version?: string | null; readonly sha256: string }
export interface E4SchemaList { readonly schemas: ReadonlyArray<E4SchemaInfo>; readonly total: number }
export interface RegistryInfo { readonly entries: number; readonly path: string; readonly registry_id: string; readonly schema_version?: string | null }
export interface RegistryList { readonly registries: ReadonlyArray<RegistryInfo>; readonly total: number }

export interface RlRunSubmitRequest { readonly run_id: string; readonly tenant_id: string; readonly workspace_id: string; readonly env_package_ref: string; readonly target_run_id: string; readonly requested_tasks?: number; readonly requested_gpus?: number; readonly requested_budget_usd?: number; readonly requested_duration_seconds?: number; readonly metadata?: Record<string, unknown> }
export interface RlRunStatus { readonly run_id: string; readonly state: string; readonly target_run_id: string; readonly accepted: boolean; readonly cancellation_state: string; readonly reason?: string }
export type RLRunStatusResponse = RlRunStatus
export interface RLRunSubmitResponse extends RlRunStatus {}
export interface RLRunCancelRequest { readonly tenant_id: string; readonly workspace_id: string; readonly reason?: string }
export type RLRunSubmitRequest = RlRunSubmitRequest
export interface RlArtifact { readonly artifact_id: string; readonly relative_path: string; readonly sha256: string; readonly bytes: number; readonly egress_allowed: boolean }
export interface RLRunArtifactListResponse { readonly artifacts: ReadonlyArray<RlArtifact>; readonly run_id: string }
export interface RLRunReplayResponse { readonly artifact_id: string; readonly available: boolean; readonly reason?: string | null; readonly replay_path?: string | null; readonly sha256?: string | null }
export interface RLRunAuditResponse { readonly run_id: string; readonly tenant_id: string; readonly workspace_id: string; readonly target_run_id: string; readonly state: string; readonly persistent_store: string; readonly scorecard_update_allowed: boolean }

export interface CTreeNode { readonly id: string; readonly digest: string; readonly kind: string; readonly turn?: number | null; readonly payload: unknown }
export interface CTreeSnapshotSummary { readonly schema_version: string; readonly node_count: number; readonly event_count: number; readonly last_id: string | null; readonly node_hash: string | null }
export interface CTreeNodeEventPayload { readonly node: CTreeNode; readonly snapshot: CTreeSnapshotSummary }
export interface CTreeSnapshotEventPayload { readonly snapshot: Record<string, unknown> | null; readonly compiler?: Record<string, unknown> | null; readonly collapse?: Record<string, unknown> | null; readonly runner?: Record<string, unknown> | null; readonly hash_summary?: Record<string, unknown> | null; readonly last_node?: CTreeNode | Record<string, unknown> | null }

export interface RlStreamEvent { readonly sequence: number; readonly run_id: string; readonly event_type: string; readonly state: string; readonly message: string; readonly target_run_id: string; readonly payload: Record<string, unknown> }
export interface RlArtifact { readonly artifact_id: string; readonly relative_path: string; readonly sha256: string; readonly bytes: number; readonly egress_allowed: boolean }
