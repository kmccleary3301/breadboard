import { describe, expect, it } from "vitest"

import { createOneShotRenderState, renderOneShotEvent } from "../oneshotRender.js"

const event = (type: string, payload: Record<string, unknown>) => ({ type, payload }) as any

describe("oneshotRender", () => {
  it("streams assistant deltas directly", () => {
    const state = createOneShotRenderState()
    expect(renderOneShotEvent(state, event("assistant.message.delta", { delta: "Hello" }))).toEqual([
      { stream: "stdout", text: "Hello" },
    ])
  })

  it("suppresses commentary by default", () => {
    const state = createOneShotRenderState()
    expect(renderOneShotEvent(state, event("assistant.thought_summary.delta", { delta: "plan" }))).toEqual([])
  })

  it("formats shell tool execution without raw tool_result json", () => {
    const state = createOneShotRenderState()
    expect(
      renderOneShotEvent(
        state,
        event("tool_call", { call_id: "c1", tool_name: "shell_command" }),
      ),
    ).toEqual([])
    expect(
      renderOneShotEvent(
        state,
        event("tool.exec.start", { call_id: "c1", tool_name: "shell_command", command: "/bin/bash -lc pwd" }),
      ),
    ).toEqual([{ stream: "stdout", text: "$ /bin/bash -lc pwd\n" }])
    expect(renderOneShotEvent(state, event("tool.exec.stdout.delta", { call_id: "c1", delta: "/tmp\n" }))).toEqual([
      { stream: "stdout", text: "/tmp\n" },
    ])
    expect(
      renderOneShotEvent(
        state,
        event("tool_result", {
          call_id: "c1",
          tool: "shell_command",
          success: true,
          status: "ok",
          metadata: { exit_code: 0 },
        }),
      ),
    ).toEqual([])
  })

  it("prints compact tool errors", () => {
    const state = createOneShotRenderState()
    renderOneShotEvent(state, event("tool_call", { call_id: "c1", tool_name: "shell_command" }))
    expect(
      renderOneShotEvent(
        state,
        event("tool_result", {
          call_id: "c1",
          tool: "shell_command",
          success: false,
          error: "permission denied",
          status: "error",
        }),
      ),
    ).toEqual([{ stream: "stderr", text: "[tool error] shell_command: permission denied\n" }])
  })

  it("drops null-like assistant placeholders from provider bridges", () => {
    const state = createOneShotRenderState()
    expect(renderOneShotEvent(state, event("assistant_message", { text: "None" }))).toEqual([])
  })

  it("suppresses assistant output that exactly mirrors single-line tool stdout", () => {
    const state = createOneShotRenderState()
    renderOneShotEvent(state, event("tool_call", { call_id: "c1", tool_name: "shell_command" }))
    renderOneShotEvent(
      state,
      event("tool.exec.start", { call_id: "c1", tool_name: "shell_command", command: "/bin/bash -lc pwd" }),
    )
    renderOneShotEvent(state, event("tool.exec.stdout.delta", { call_id: "c1", delta: "/tmp/project\n" }))
    renderOneShotEvent(
      state,
      event("tool_result", {
        call_id: "c1",
        tool: "shell_command",
        success: true,
        status: "ok",
        metadata: { exit_code: 0 },
      }),
    )
    expect(renderOneShotEvent(state, event("assistant.message.delta", { delta: "/tmp/project\n" }))).toEqual([])
  })

  it("emits only the suffix when assistant output extends mirrored tool stdout", () => {
    const state = createOneShotRenderState()
    renderOneShotEvent(state, event("tool_call", { call_id: "c1", tool_name: "shell_command" }))
    renderOneShotEvent(
      state,
      event("tool.exec.start", { call_id: "c1", tool_name: "shell_command", command: "/bin/bash -lc pwd" }),
    )
    renderOneShotEvent(state, event("tool.exec.stdout.delta", { call_id: "c1", delta: "/tmp/project\n" }))
    renderOneShotEvent(
      state,
      event("tool_result", {
        call_id: "c1",
        tool: "shell_command",
        success: true,
        status: "ok",
        metadata: { exit_code: 0 },
      }),
    )
    expect(renderOneShotEvent(state, event("assistant.message.delta", { delta: "/tmp/project\nDone.\n" }))).toEqual([
      { stream: "stdout", text: "Done.\n" },
    ])
  })
})
