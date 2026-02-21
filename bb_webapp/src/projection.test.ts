import { describe, expect, it } from "vitest"
import type { SessionEvent } from "@breadboard/sdk"
import { applyEventToProjection, initialProjectionState, PROJECTION_LIMITS } from "./projection"

const makeEvent = (partial: Partial<SessionEvent> & Pick<SessionEvent, "id" | "type">): SessionEvent => ({
  id: partial.id,
  type: partial.type,
  session_id: partial.session_id ?? "session-1",
  turn: partial.turn ?? null,
  timestamp: partial.timestamp ?? Date.now(),
  payload: partial.payload ?? {},
  seq: partial.seq,
  timestamp_ms: partial.timestamp_ms,
  run_id: partial.run_id,
  thread_id: partial.thread_id,
  turn_id: partial.turn_id,
})

describe("projection reducer", () => {
  it("builds assistant streaming rows from deltas and finalizes on end", () => {
    const start = applyEventToProjection(
      initialProjectionState,
      makeEvent({
        id: "e1",
        type: "assistant.message.delta",
        payload: { delta: "Hello " },
      }),
    )
    const next = applyEventToProjection(
      start,
      makeEvent({
        id: "e2",
        type: "assistant.message.delta",
        payload: { delta: "world" },
      }),
    )
    const done = applyEventToProjection(
      next,
      makeEvent({
        id: "e3",
        type: "assistant.message.end",
      }),
    )

    expect(done.transcript).toHaveLength(1)
    expect(done.transcript[0].role).toBe("assistant")
    expect(done.transcript[0].text).toBe("Hello world")
    expect(done.transcript[0].final).toBe(true)
  })

  it("records tool calls and tool results as structured rows", () => {
    const called = applyEventToProjection(
      initialProjectionState,
      makeEvent({
        id: "tool-1",
        type: "tool_call",
        payload: { tool_name: "exec_command", cmd: "pwd" },
      }),
    )
    const done = applyEventToProjection(
      called,
      makeEvent({
        id: "tool-2",
        type: "tool_result",
        payload: { tool_name: "exec_command", output: "/tmp" },
      }),
    )

    expect(done.toolRows).toHaveLength(2)
    expect(done.toolRows[0].type).toBe("tool_call")
    expect(done.toolRows[1].type).toBe("tool_result")
  })

  it("deduplicates duplicate events by (session_id, type, id)", () => {
    const event = makeEvent({
      id: "dup-1",
      type: "user_message",
      payload: { text: "hello" },
    })
    const once = applyEventToProjection(initialProjectionState, event)
    const twice = applyEventToProjection(once, event)

    expect(twice.events).toHaveLength(1)
    expect(twice.transcript).toHaveLength(1)
  })

  it("tracks permission requests and clears them on response", () => {
    const requestState = applyEventToProjection(
      initialProjectionState,
      makeEvent({
        id: "p1",
        type: "permission_request",
        payload: {
          request_id: "permission_1",
          tool: "bash",
          kind: "shell",
          summary: "Permission needed",
          rule_suggestion: "npm install *",
          default_scope: "project",
        },
      }),
    )
    expect(requestState.pendingPermissions).toHaveLength(1)
    expect(requestState.pendingPermissions[0].requestId).toBe("permission_1")
    expect(requestState.pendingPermissions[0].tool).toBe("bash")

    const responseState = applyEventToProjection(
      requestState,
      makeEvent({
        id: "p2",
        type: "permission_response",
        payload: {
          request_id: "permission_1",
          response: "once",
        },
      }),
    )
    expect(responseState.pendingPermissions).toHaveLength(0)
  })

  it("enforces deterministic memory bounds for events, transcript, tools, and permissions", () => {
    let state = initialProjectionState

    for (let index = 0; index < PROJECTION_LIMITS.events + 50; index += 1) {
      state = applyEventToProjection(
        state,
        makeEvent({
          id: `u-${index}`,
          type: "user_message",
          payload: { text: `message-${index}` },
          seq: index + 1,
        }),
      )
    }
    expect(state.events.length).toBe(PROJECTION_LIMITS.events)
    expect(state.transcript.length).toBe(PROJECTION_LIMITS.transcript)

    for (let index = 0; index < PROJECTION_LIMITS.toolRows + 20; index += 1) {
      state = applyEventToProjection(
        state,
        makeEvent({
          id: `t-${index}`,
          type: "tool_call",
          payload: { tool_name: "exec_command", index },
        }),
      )
    }
    expect(state.toolRows.length).toBe(PROJECTION_LIMITS.toolRows)

    for (let index = 0; index < PROJECTION_LIMITS.pendingPermissions + 20; index += 1) {
      state = applyEventToProjection(
        state,
        makeEvent({
          id: `p-${index}`,
          type: "permission_request",
          payload: {
            request_id: `permission-${index}`,
            tool: "bash",
            kind: "shell",
            summary: "permission",
          },
        }),
      )
    }
    expect(state.pendingPermissions.length).toBe(PROJECTION_LIMITS.pendingPermissions)
  })
})
