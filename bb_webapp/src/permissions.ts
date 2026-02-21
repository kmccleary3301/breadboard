import type { PermissionRequestRow, PermissionScope } from "./projection"

export type PermissionDraft = {
  note: string
  rule: string
  scope: PermissionScope
}

export type PermissionDecision = "allow-once" | "allow-always" | "deny-once" | "deny-always" | "deny-stop"

export const buildPermissionDecisionPayload = (
  request: PermissionRequestRow,
  draft: PermissionDraft,
  decision: PermissionDecision,
): Record<string, unknown> => {
  const payload: Record<string, unknown> = {
    request_id: request.requestId,
    decision,
  }
  const note = draft.note.trim()
  if (note) payload.note = note
  if (decision === "allow-always" || decision === "deny-always") {
    payload.scope = draft.scope
    const rule = (draft.rule || request.ruleSuggestion || "").trim()
    if (rule) payload.rule = rule
  }
  if (decision === "deny-stop") {
    payload.stop = true
  }
  return payload
}
