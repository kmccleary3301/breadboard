export const FORBIDDEN_ROUTE_MARKERS = ["/events/sse", "/artifacts/download", "/user_message"] as const
export const FORBIDDEN_COMMAND_ALIASES = [
  "checkpoint_list",
  "checkpoints_list",
  "checkpoint_restore",
  "checkpoint_restored",
] as const

export const buildSessionDownloadPath = (sessionId: string): string => `/sessions/${sessionId}/download`

export const NORMALIZED_ROUTE_CATALOG = [
  "/health",
  "/status",
  "/sessions",
  "/sessions/{session_id}",
  "/sessions/{session_id}/input",
  "/sessions/{session_id}/command",
  "/sessions/{session_id}/attachments",
  "/sessions/{session_id}/files",
  "/sessions/{session_id}/download",
  "/sessions/{session_id}/skills",
  "/sessions/{session_id}/ctrees",
  "/sessions/{session_id}/events",
] as const

export const NORMALIZED_COMMAND_CATALOG = ["list_checkpoints", "restore_checkpoint", "permission_decision"] as const
