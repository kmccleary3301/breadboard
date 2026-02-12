import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"
import {
  collectTranscriptNoiseMetrics,
  evaluateTranscriptNoiseGate,
  renderTranscriptNoiseGateJson,
} from "../transcriptNoiseGate.js"

describe("transcriptNoiseGate", () => {
  it("passes when canonical transcript dominates noise surfaces", () => {
    const state = {
      conversation: [
        { speaker: "user", text: "Write a short summary of this document." },
        { speaker: "assistant", text: "Here is a concise summary with key points and outcomes." },
      ],
      toolEvents: [{ text: "[status] Thinking..." }],
      hints: ["Run started."],
    }
    const result = evaluateTranscriptNoiseGate(state, 0.9)
    expect(result.ok).toBe(true)
    expect(result.metrics.canonicalChars).toBeGreaterThan(result.metrics.noiseChars)
  })

  it("fails when tool/hint surfaces overwhelm canonical transcript", () => {
    const state = {
      conversation: [{ speaker: "assistant", text: "ok" }],
      toolEvents: [{ text: "[tool] ".padEnd(150, "x") }],
      hints: ["hint".padEnd(150, "y")],
    }
    const result = evaluateTranscriptNoiseGate(state, 0.5)
    expect(result.ok).toBe(false)
    expect(result.summary).toContain("ratio:")
  })

  it("collects machine-readable metrics from a real fixture-driven state", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }
    controller.applyEvent({ id: "1", seq: 1, turn: 1, type: "run.start", payload: {} })
    controller.applyEvent({ id: "2", seq: 2, turn: 1, type: "assistant.message.start", payload: {} })
    controller.applyEvent({ id: "3", seq: 3, turn: 1, type: "assistant.message.delta", payload: { delta: "hello world" } })
    controller.applyEvent({ id: "4", seq: 4, turn: 1, type: "completion", payload: { completed: true } })

    const metrics = collectTranscriptNoiseMetrics(controller.getState())
    expect(metrics.canonicalChars).toBeGreaterThan(0)
    expect(metrics.ratio).toBeGreaterThanOrEqual(0)

    const result = evaluateTranscriptNoiseGate(controller.getState(), 1.5)
    const json = renderTranscriptNoiseGateJson(result)
    expect(() => JSON.parse(json)).not.toThrow()
  })
})
