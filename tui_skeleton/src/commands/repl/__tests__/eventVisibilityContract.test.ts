import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const makeController = () =>
  new ReplSessionController({
    configPath: "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
    workspace: ".",
    model: null,
    remotePreference: null,
    permissionMode: null,
  }) as any

describe("ReplSessionController event visibility contract", () => {
  it("does not project audit assistant payloads into visible conversation", () => {
    const controller = makeController()

    controller.applyEvent({
      id: "audit-1",
      seq: 1,
      type: "assistant_message",
      visibility: "audit",
      payload: { text: "You are Codex, based on GPT-5. Private prompt text." },
    })

    const state = controller.getState()
    expect(state.conversation).toHaveLength(0)
  })

  it("projects explicit transcript assistant payloads into visible conversation", () => {
    const controller = makeController()

    controller.applyEvent({
      id: "msg-1",
      seq: 1,
      type: "assistant_message",
      visibility: "transcript",
      payload: { text: "visible answer" },
    })

    const state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]).toMatchObject({ speaker: "assistant", text: "visible answer" })
  })

  it("does not turn completion summaries into conversation unless explicitly transcript-visible", () => {
    const controller = makeController()

    controller.applyEvent({
      id: "done-1",
      seq: 1,
      type: "completion",
      visibility: "host",
      payload: {
        summary: { completed: true },
        final_message: "TASK COMPLETE",
      },
    })

    expect(controller.getState().conversation).toHaveLength(0)

    controller.applyEvent({
      id: "done-2",
      seq: 2,
      type: "completion",
      visibility: "host",
      payload: {
        summary: { completed: true },
        display: { visibility: "transcript" },
        final_message: "visible completion notice",
      },
    })

    const state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]).toMatchObject({ speaker: "system", text: "visible completion notice" })
  })

  it("does not project log links into readable tool transcript rows", () => {
    const controller = makeController()

    controller.applyEvent({
      id: "log-1",
      seq: 1,
      type: "log_link",
      visibility: "host",
      payload: { url: "file://logging/20260430-203935_ray_SCE" },
    })

    const state = controller.getState()
    expect(state.toolEvents).toHaveLength(0)
    expect(state.hints).toHaveLength(0)
  })
})
