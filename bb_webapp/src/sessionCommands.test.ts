import { describe, expect, it } from "vitest"
import { requestCheckpointList, requestCheckpointRestore, requestPermissionRevoke } from "./sessionCommands"

describe("sessionCommands", () => {
  it("uses canonical checkpoint list command", async () => {
    const attempted: string[] = []
    const post = async (command: string) => {
      attempted.push(command)
      return { status: "ok" }
    }
    const result = await requestCheckpointList(post)
    expect(result.status).toBe("ok")
    expect(attempted).toEqual(["list_checkpoints"])
  })

  it("passes restore checkpoint payload with optional mode/prune", async () => {
    const attempted: Array<{ command: string; payload?: Record<string, unknown> }> = []
    const result = await requestCheckpointRestore(
      async (command, payload) => {
        attempted.push({ command, payload })
        return { status: String(payload?.checkpoint_id ?? "missing") }
      },
      "cp-1",
      { mode: "both", prune: false },
    )
    expect(result.status).toBe("cp-1")
    expect(attempted).toEqual([
      { command: "restore_checkpoint", payload: { checkpoint_id: "cp-1", mode: "both", prune: false } },
    ])
  })

  it("uses legacy permission revoke fallback if canonical decision command fails", async () => {
    const attempted: string[] = []
    const result = await requestPermissionRevoke(
      async (command) => {
        attempted.push(command)
        if (command === "permission_decision") throw new Error("not supported")
        return { status: "ok" }
      },
      { requestId: "p1", tool: "bash", scope: "project", rule: "npm *" },
    )
    expect(result.status).toBe("ok")
    expect(attempted).toEqual(["permission_decision", "permission_revoke"])
  })

  it("throws when permission revoke variants are both unsupported", async () => {
    await expect(
      requestPermissionRevoke(
        async () => {
          throw new Error("unsupported")
        },
        { requestId: "p1", tool: "bash", scope: "project", rule: null },
      ),
    ).rejects.toThrow("unsupported")
  })
})
