import { describe, it, expect } from "vitest"
import { ReplSessionController } from "../src/commands/repl/controller.js"

describe("ReplSessionController", () => {
  it("waitForCompletion resolves when completionSeen toggles", async () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
    })
    // Pretend an active session exists
    // @ts-expect-error mutating private field in test
    controller.sessionId = "test-session"

    const promise = controller.waitForCompletion(1000)
    setTimeout(() => {
      // @ts-expect-error mutating private field in test
      controller.completionSeen = true
      // @ts-expect-error invoking private helper
      controller.emitChange()
    }, 10)

    await expect(promise).resolves.toBeUndefined()
  })

  it("clears queued permissions on deny-stop", async () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
    })
    // @ts-expect-error mutating private field in test
    controller.sessionId = "test-session"
    // @ts-expect-error override API call for test
    controller.runSessionCommand = async () => true

    const request = {
      requestId: "perm-1",
      tool: "write_files",
      kind: "edit",
      rewindable: true,
      summary: "Permission required",
      diffText: null,
      ruleSuggestion: null,
      defaultScope: "project",
      createdAt: Date.now(),
    }

    // @ts-expect-error mutating private field in test
    controller.permissionActive = request
    // @ts-expect-error mutating private field in test
    controller.permissionQueue = [{ ...request, requestId: "perm-2" }]

    await expect(controller.respondToPermission({ kind: "deny-stop" })).resolves.toBe(true)
    const state = controller.getState()
    expect(state.permissionRequest).toBeNull()
    expect(state.permissionQueueDepth).toBe(0)
  })
})
