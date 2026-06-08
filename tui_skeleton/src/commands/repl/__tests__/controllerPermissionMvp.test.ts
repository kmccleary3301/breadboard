import { describe, expect, it, vi } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (
  seq: number,
  type: string,
  payload: Record<string, unknown> = {},
  extra: Record<string, unknown> = {},
) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
  ...extra,
})

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/misc/test_simple_native.yaml",
    workspace: ".",
    permissionMode: "on-request",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
    respondToPermission: (decision: any) => Promise<boolean>
    runSessionCommand: any
    sessionId: string
  }

describe("permission approval MVP semantics", () => {
  it("normalizes permission request provenance from payload, action, and actor envelope", () => {
    const controller = createController()

    controller.applyEvent(
      event(
        1,
        "permission.request",
        {
          request_id: "perm-1",
          tool: "Bash",
          kind: "shell",
          summary: "Run test command",
          reason: "User asked for project tests",
          cwd: "/tmp/bb-permission-mvp",
          scope: "project",
          engine_permission_mode: "approval-required",
          rule_suggestion: "bash:test",
          action: {
            command: "pnpm test",
            task_id: "task-7",
            task_label: "test sweep",
          },
        },
        {
          actor: {
            id: "agent-2",
            label: "reviewer agent",
          },
        },
      ),
    )

    const request = controller.getState().permissionRequest
    expect(request).toMatchObject({
      requestId: "perm-1",
      tool: "Bash",
      kind: "shell",
      summary: "Run test command",
      reason: "User asked for project tests",
      cwd: "/tmp/bb-permission-mvp",
      defaultScope: "project",
      effectiveScope: "project",
      launchPermissionMode: "on-request",
      enginePermissionMode: "approval-required",
      agentId: "agent-2",
      agentLabel: "reviewer agent",
      taskId: "task-7",
      taskLabel: "test sweep",
      ruleSuggestion: "bash:test",
    })
  })

  it("sends scoped permission decisions with notes and advances queued requests", async () => {
    const controller = createController()
    controller.sessionId = "session-1"
    const sent: Array<{ command: string; payload?: Record<string, unknown> }> = []
    controller.runSessionCommand = vi.fn(async (command: string, payload?: Record<string, unknown>) => {
      sent.push({ command, payload })
      return true
    })

    controller.applyEvent(event(1, "permission.request", { request_id: "perm-1", tool: "Write" }))
    controller.applyEvent(event(2, "permission.request", { request_id: "perm-2", tool: "Bash" }))

    await controller.respondToPermission({
      kind: "deny-always",
      scope: "project",
      rule: "write:secrets",
      note: "Do not modify secrets.",
    })

    expect(sent).toEqual([
      {
        command: "permission_decision",
        payload: {
          request_id: "perm-1",
          decision: "deny-always",
          scope: "project",
          rule: "write:secrets",
          note: "Do not modify secrets.",
        },
      },
    ])
    expect(controller.getState().permissionRequest?.requestId).toBe("perm-2")
    expect(controller.getState().permissionQueueDepth).toBe(0)
  })

  it("sends deny-stop as a stop decision and clears active plus queued requests", async () => {
    const controller = createController()
    controller.sessionId = "session-1"
    const sent: Array<{ command: string; payload?: Record<string, unknown> }> = []
    controller.runSessionCommand = vi.fn(async (command: string, payload?: Record<string, unknown>) => {
      sent.push({ command, payload })
      return true
    })

    controller.applyEvent(event(1, "permission.request", { request_id: "perm-1", tool: "Write" }))
    controller.applyEvent(event(2, "permission.request", { request_id: "perm-2", tool: "Bash" }))

    await controller.respondToPermission({ kind: "deny-stop", note: "Stop this turn." })

    expect(sent[0]).toEqual({
      command: "permission_decision",
      payload: {
        request_id: "perm-1",
        decision: "deny-stop",
        note: "Stop this turn.",
        stop: true,
      },
    })
    expect(controller.getState().permissionRequest).toBeNull()
    expect(controller.getState().permissionQueueDepth).toBe(0)
    expect(controller.getState().pendingResponse).toBe(false)
    expect(controller.getState().status).toBe("Stopped (permission denied)")
  })

  it("surfaces retryable permission errors as halted error states", () => {
    const controller = createController()

    controller.applyEvent(event(1, "permission.request", { request_id: "perm-1", tool: "Bash" }))
    controller.applyEvent(
      event(2, "permission.decision", {
        decision: "allow-once",
        status: "error",
        error: "engine rejected stale request",
        retryable: true,
      }),
    )

    const state = controller.getState()
    expect(state.permissionRequest).toBeNull()
    expect(state.pendingResponse).toBe(false)
    expect(state.activity?.primary).toBe("halted")
    expect(state.activity?.label).toBe("Permission error (retryable)")
    expect(state.toolEvents.some((entry: any) => String(entry.text).includes("retryable"))).toBe(true)
    expect(state.toolEvents.some((entry: any) => String(entry.text).includes("engine rejected stale request"))).toBe(true)
  })
})
