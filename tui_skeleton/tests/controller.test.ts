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

  it("inserts tool_result immediately after matching tool_call", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
    })
    // @ts-expect-error mutating private field in test
    controller.sessionId = "test-session"

    // @ts-expect-error invoke private helper
    controller.applyEvent({
      id: "evt-call",
      type: "tool_call",
      session_id: "test-session",
      turn: 1,
      timestamp: 0,
      payload: { call_id: "call-1", tool: "run_shell", action: "start" },
    })
    // @ts-expect-error invoke private helper
    controller.applyEvent({
      id: "evt-result",
      type: "tool_result",
      session_id: "test-session",
      turn: 1,
      timestamp: 1,
      payload: { call_id: "call-1", status: "ok", error: false, result: { exit: 0 } },
    })

    const events = controller.getState().toolEvents
    const callIndex = events.findIndex((entry) => entry.text.includes("[call]"))
    const resultIndex = events.findIndex((entry) => entry.text.includes("[result]"))
    expect(callIndex).toBeGreaterThanOrEqual(0)
    expect(resultIndex).toBe(callIndex + 1)
  })

  it("replays delta streams deterministically", () => {
    const events = [
      {
        id: "evt-1",
        type: "assistant.message.start",
        session_id: "test-session",
        turn: 1,
        timestamp: 1,
        payload: { message_id: "m1", channel: "text", format: "markdown" },
      },
      {
        id: "evt-2",
        type: "assistant.message.delta",
        session_id: "test-session",
        turn: 1,
        timestamp: 2,
        payload: { message_id: "m1", delta: "Hello " },
      },
      {
        id: "evt-3",
        type: "assistant.message.delta",
        session_id: "test-session",
        turn: 1,
        timestamp: 3,
        payload: { message_id: "m1", delta: "world" },
      },
      {
        id: "evt-4",
        type: "assistant.message.end",
        session_id: "test-session",
        turn: 1,
        timestamp: 4,
        payload: { message_id: "m1", stop_reason: "end_turn" },
      },
      {
        id: "evt-5",
        type: "assistant.tool_call.start",
        session_id: "test-session",
        turn: 1,
        timestamp: 5,
        payload: { tool_call_id: "call-1", tool_name: "write_file" },
      },
      {
        id: "evt-6",
        type: "assistant.tool_call.delta",
        session_id: "test-session",
        turn: 1,
        timestamp: 6,
        payload: { tool_call_id: "call-1", args_text_delta: "{\"path\":\"notes.txt\"" },
      },
      {
        id: "evt-7",
        type: "assistant.tool_call.delta",
        session_id: "test-session",
        turn: 1,
        timestamp: 7,
        payload: { tool_call_id: "call-1", args_text_delta: ",\"content\":\"Hello\"}" },
      },
      {
        id: "evt-8",
        type: "assistant.tool_call.end",
        session_id: "test-session",
        turn: 1,
        timestamp: 8,
        payload: { tool_call_id: "call-1", tool_name: "write_file" },
      },
    ]

    const runReplay = () => {
      const controller = new ReplSessionController({
        configPath: "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
      })
      // @ts-expect-error mutating private field in test
      controller.sessionId = "test-session"
      for (const event of events) {
        // @ts-expect-error invoking private helper
        controller.applyEvent(event)
      }
      const state = controller.getState()
      return {
        conversation: state.conversation.map((entry) => ({
          speaker: entry.speaker,
          text: entry.text,
          phase: entry.phase,
        })),
        toolEvents: state.toolEvents.map((entry) => entry.text),
      }
    }

    const first = runReplay()
    const second = runReplay()

    expect(first).toEqual(second)
    expect(first.conversation).toEqual([
      { speaker: "assistant", text: "Hello world", phase: "final" },
    ])
    expect(first.toolEvents.some((text) => text.includes("\"path\":\"notes.txt\""))).toBe(true)
    expect(first.toolEvents.some((text) => text.includes("\"content\":\"Hello\""))).toBe(true)
  })

  it("reduces ctree events into model state", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/opencode_grok4fast_c_fs_v2.yaml",
    })
    // @ts-expect-error mutating private field in test
    controller.sessionId = "test-session"

    const snapshot = {
      schema_version: "0.1",
      node_count: 1,
      event_count: 1,
      last_id: "n1",
      node_hash: "h1",
    }
    const node = { id: "n1", digest: "d1", kind: "message", turn: 1, payload: { text: "hello" } }

    // @ts-expect-error invoke private helper
    controller.applyEvent({
      id: "evt-ctree-node",
      type: "ctree_node",
      session_id: "test-session",
      turn: 1,
      timestamp: 1,
      payload: { node, snapshot },
    })

    // @ts-expect-error invoke private helper
    controller.applyEvent({
      id: "evt-ctree-snapshot",
      type: "ctree_snapshot",
      session_id: "test-session",
      turn: 1,
      timestamp: 2,
      payload: { snapshot },
    })

    const state = controller.getState()
    expect(state.ctreeModel).toBeTruthy()
    expect(state.ctreeModel?.nodesById["n1"]).toEqual(node)
    expect(state.ctreeModel?.nodeOrder).toEqual(["n1"])
    expect(state.ctreeModel?.snapshot).toEqual(snapshot)
    expect(state.ctreeSnapshot?.snapshot).toEqual(snapshot)
  })
})
