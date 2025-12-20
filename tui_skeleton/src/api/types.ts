export type EventType =
  | "turn_start"
  | "assistant_message"
  | "user_message"
  | "tool_call"
  | "tool_result"
  | "permission_request"
  | "checkpoint_list"
  | "checkpoint_restored"
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
