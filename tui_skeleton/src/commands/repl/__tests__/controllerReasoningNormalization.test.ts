import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
    runtimeFlags: Record<string, unknown>
    providerCapabilities: Record<string, unknown>
  }

describe("ReplSessionController reasoning normalization", () => {
  it("normalizes OpenAI, Anthropic, and fallback reasoning payload shapes", () => {
    const fixtures = [
      {
        provider: "openai",
        payload: { summary: [{ text: "openai reason summary" }] },
      },
      {
        provider: "anthropic",
        payload: { content_block: { thinking: "anthropic thought block" } },
      },
      {
        provider: "unknown",
        payload: { delta: "fallback reasoning delta" },
      },
    ]

    for (const [index, fixture] of fixtures.entries()) {
      const controller = createController()
      controller.runtimeFlags = {
        ...controller.runtimeFlags,
        thinkingEnabled: true,
        thinkingPreviewEnabled: true,
        thinkingPreviewMaxLines: 5,
        thinkingPreviewTtlMs: 1200,
      }
      controller.providerCapabilities = {
        ...controller.providerCapabilities,
        provider: fixture.provider,
        reasoningEvents: true,
        thoughtSummaryEvents: true,
      }

      controller.applyEvent(event(1, "turn_start"))
      controller.applyEvent(event(2, "assistant.thought_summary.delta", fixture.payload))
      const state = controller.getState()
      const summary = String(state.thinkingArtifact?.summary ?? "")
      expect(summary.length).toBeGreaterThan(0)
      expect(state.thinkingPreview?.lines?.length ?? 0).toBeGreaterThan(0)
      expect(state.runtimeTelemetry?.thinkingUpdates ?? 0).toBe(1)
      expect(state.runtimeTelemetry?.thinkingPreviewOpened ?? 0).toBeGreaterThanOrEqual(1)
      if (index === 0) {
        expect(summary).toContain("openai reason")
      } else if (index === 1) {
        expect(summary).toContain("anthropic thought")
      } else {
        expect(summary).toContain("fallback reasoning")
      }
    }
  })
})
