import type { SessionCommandResponse } from "@breadboard/sdk"

export type CommandPoster = (command: string, payload?: Record<string, unknown>) => Promise<SessionCommandResponse>

export type CheckpointRestoreMode = "code" | "conversation" | "both"

const runWithFallbacks = async (
  post: CommandPoster,
  commands: Array<{ command: string; payload?: Record<string, unknown> }>,
): Promise<SessionCommandResponse> => {
  let lastError: unknown = null
  for (const row of commands) {
    try {
      return await post(row.command, row.payload)
    } catch (error) {
      lastError = error
    }
  }
  throw lastError ?? new Error("command failed")
}

export const requestCheckpointList = async (post: CommandPoster): Promise<SessionCommandResponse> =>
  await runWithFallbacks(post, [{ command: "list_checkpoints" }])

export const requestCheckpointRestore = async (
  post: CommandPoster,
  checkpointId: string,
  options?: { mode?: CheckpointRestoreMode; prune?: boolean },
): Promise<SessionCommandResponse> =>
  await runWithFallbacks(post, [
    {
      command: "restore_checkpoint",
      payload: {
        checkpoint_id: checkpointId,
        ...(options?.mode ? { mode: options.mode } : {}),
        ...(typeof options?.prune === "boolean" ? { prune: options.prune } : {}),
      },
    },
  ])

export const requestPermissionRevoke = async (
  post: CommandPoster,
  payload: { requestId: string; tool: string; scope: string; rule: string | null },
): Promise<SessionCommandResponse> =>
  await runWithFallbacks(post, [
    {
      command: "permission_decision",
      payload: {
        request_id: payload.requestId,
        decision: "revoke",
        rule: payload.rule ?? undefined,
        scope: payload.scope,
      },
    },
    {
      command: "permission_revoke",
      payload: {
        request_id: payload.requestId,
        tool: payload.tool,
        scope: payload.scope,
        rule: payload.rule ?? undefined,
      },
    },
  ])
