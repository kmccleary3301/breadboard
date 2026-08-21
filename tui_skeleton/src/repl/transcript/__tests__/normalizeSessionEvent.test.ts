import type { SessionEvent } from "@breadboard/sdk"
import { describe, expect, it } from "vitest"
import { normalizeSessionEvent } from "../normalizeSessionEvent.js"

const event = (type: SessionEvent["type"], payload: SessionEvent["payload"]): SessionEvent =>
  ({
    id: `event-${type}`,
    type,
    session_id: "session-1",
    timestamp: 1,
    payload,
  }) as SessionEvent

describe("normalizeSessionEvent", () => {
  it("preserves canonical streamed tool-call arguments", () => {
    const delta = normalizeSessionEvent(
      event("assistant.tool_call.delta", {
        call_id: "call-1",
        tool: "run_shell",
        arguments_delta: '{"command":',
      }),
    )
    const completed = normalizeSessionEvent(
      event("assistant.tool_call.end", {
        call_id: "call-1",
        tool: "run_shell",
        arguments: '{"command":"pwd"}',
      }),
    )

    expect(delta).toMatchObject({
      toolCallId: "call-1",
      toolName: "run_shell",
      textDelta: '{"command":',
    })
    expect(completed).toMatchObject({
      toolCallId: "call-1",
      toolName: "run_shell",
      argsText: '{"command":"pwd"}',
    })
  })
})
