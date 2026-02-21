import type { SessionCommandResponse } from "@breadboard/sdk"

export type CommandPoster = (command: string, payload?: Record<string, unknown>) => Promise<SessionCommandResponse>

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
  await runWithFallbacks(post, [
    { command: "checkpoint_list" },
    { command: "checkpoints_list" },
    { command: "list_checkpoints" },
  ])

export const requestCheckpointRestore = async (
  post: CommandPoster,
  checkpointId: string,
): Promise<SessionCommandResponse> =>
  await runWithFallbacks(post, [
    { command: "checkpoint_restore", payload: { checkpoint_id: checkpointId } },
    { command: "restore_checkpoint", payload: { checkpoint_id: checkpointId } },
    { command: "checkpoint_restored", payload: { checkpoint_id: checkpointId } },
  ])

export const requestPermissionRevoke = async (
  post: CommandPoster,
  payload: { requestId: string; tool: string; scope: string; rule: string | null },
): Promise<SessionCommandResponse> =>
  await runWithFallbacks(post, [
    {
      command: "permission_revoke",
      payload: {
        request_id: payload.requestId,
        tool: payload.tool,
        scope: payload.scope,
        rule: payload.rule ?? undefined,
      },
    },
    {
      command: "permission_decision",
      payload: {
        request_id: payload.requestId,
        decision: "revoke",
        scope: payload.scope,
        rule: payload.rule ?? undefined,
      },
    },
  ])
