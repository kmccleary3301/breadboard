import { describe, expect, it } from "vitest"
import { requestCheckpointList, requestCheckpointRestore, requestPermissionRevoke } from "./sessionCommands"

describe("sessionCommands", () => {
  it("falls back through checkpoint list command aliases", async () => {
    const attempted: string[] = []
    const post = async (command: string) => {
      attempted.push(command)
      if (command !== "list_checkpoints") throw new Error("unsupported")
      return { status: "ok" }
    }
    const result = await requestCheckpointList(post)
    expect(result.status).toBe("ok")
    expect(attempted).toEqual(["checkpoint_list", "checkpoints_list", "list_checkpoints"])
  })

  it("passes restore checkpoint id payload", async () => {
    const result = await requestCheckpointRestore(async (_command, payload) => ({ status: String(payload?.checkpoint_id ?? "missing") }), "cp-1")
    expect(result.status).toBe("cp-1")
  })

  it("uses permission revoke fallback if primary command fails", async () => {
    const attempted: string[] = []
    const result = await requestPermissionRevoke(
      async (command) => {
        attempted.push(command)
        if (command === "permission_revoke") throw new Error("not supported")
        return { status: "ok" }
      },
      { requestId: "p1", tool: "bash", scope: "project", rule: "npm *" },
    )
    expect(result.status).toBe("ok")
    expect(attempted).toEqual(["permission_revoke", "permission_decision"])
  })
})
