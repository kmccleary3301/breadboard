import { describe, expect, it } from "vitest"

import type { ConversationEntry, ToolLogEntry } from "../../../../types.js"
import { shouldSuppressAssistantToolEcho } from "../displayProjectionPolicy.js"

const assistant = (text: string, createdAt = 10): ConversationEntry => ({
  id: `a-${createdAt}`,
  speaker: "assistant",
  text,
  phase: "final",
  createdAt,
})

const tool = (detail: string[], summary = "stdout 1 line", createdAt = 9): ToolLogEntry => ({
  id: `t-${createdAt}`,
  kind: "result",
  text: "shell_command",
  status: "success",
  createdAt,
  display: {
    title: "shell_command",
    summary,
    detail,
  },
})

describe("shouldSuppressAssistantToolEcho", () => {
  it("suppresses exact single-line assistant echoes of the latest tool stdout", () => {
    expect(
      shouldSuppressAssistantToolEcho(
        assistant("/tmp/project"),
        [tool(["/tmp/project"])],
      ),
    ).toBe(true)
  })

  it("does not suppress multiline assistant responses", () => {
    expect(
      shouldSuppressAssistantToolEcho(
        assistant("/tmp/project\nDone."),
        [tool(["/tmp/project"])],
      ),
    ).toBe(false)
  })

  it("does not suppress when stderr is present", () => {
    expect(
      shouldSuppressAssistantToolEcho(
        assistant("/tmp/project"),
        [tool(["stdout:", "/tmp/project", "", "stderr:", "warn"], "stdout 1 line · stderr 1 line")],
      ),
    ).toBe(false)
  })

  it("does not suppress when the latest nearby tool output differs", () => {
    expect(
      shouldSuppressAssistantToolEcho(
        assistant("/tmp/project"),
        [tool(["/tmp/other"])],
      ),
    ).toBe(false)
  })
})
