import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../controller.js"
import { validateRuntimeScenario } from "../controllerRuntimeScenarioValidator.js"
import { evaluateTranscriptNoiseGate } from "../transcriptNoiseGate.js"

type RawEvent = Record<string, unknown>

const parseEvent = (raw: string): RawEvent | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") return parsed.event as RawEvent
    if (parsed.data && typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") return inner as RawEvent
      } catch {
        return null
      }
    }
    if (parsed.type) return parsed as RawEvent
  }
  return null
}

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
    runtimeFlags: Record<string, unknown>
    viewPrefs: Record<string, unknown>
  }

const applyFixture = (
  fixtureName: string,
  configure?: (controller: ReturnType<typeof createController>) => void,
): any => {
  const controller = createController()
  if (configure) configure(controller)
  const fixturePath = path.resolve("src/commands/repl/__tests__/fixtures", `${fixtureName}.jsonl`)
  const lines = readFileSync(fixturePath, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    controller.applyEvent(event)
  }
  return controller.getState()
}

describe("runtime tier1 deterministic fixtures gate", () => {
  it("keeps strict runtime warnings empty for subagent churn and resize-overlay fixtures", () => {
    const fixtureNames = ["subagents_strip_churn_v1", "resize_overlay_interaction_v1"]
    for (const fixtureName of fixtureNames) {
      const state = applyFixture(fixtureName)
      const strictResult = validateRuntimeScenario(state, "strict")
      const noiseResult = evaluateTranscriptNoiseGate(state, 100)
      const railText = state.toolEvents.map((entry: any) => String(entry.text ?? "")).join("\n")

      expect(strictResult.ok, fixtureName).toBe(true)
      expect(state.runtimeTelemetry?.illegalTransitions ?? 0, fixtureName).toBe(0)
      expect(noiseResult.ok, fixtureName).toBe(true)
      expect(railText, fixtureName).not.toContain("Cannot read properties of undefined (reading 'length')")
      expect(railText, fixtureName).not.toContain("buildModalStack.tsx:1084")
      expect(railText, fixtureName).not.toContain("UnhandledPromiseRejection")
    }
  })

  it("expires thinking preview for lifecycle fixture when ttl is zero", () => {
    const state = applyFixture("thinking_lifecycle_expiration_v1", (controller) => {
      controller.viewPrefs = { ...controller.viewPrefs, showReasoning: false }
      controller.runtimeFlags = {
        ...controller.runtimeFlags,
        thinkingEnabled: true,
        thinkingPreviewEnabled: true,
        thinkingPreviewTtlMs: 0,
        statusUpdateMs: 0,
      }
    })

    const strictResult = validateRuntimeScenario(state, "strict")
    expect(strictResult.ok).toBe(true)
    expect(state.runtimeTelemetry?.illegalTransitions ?? 0).toBe(0)
    expect(state.thinkingPreview).toBeNull()
    expect(state.runtimeTelemetry?.thinkingPreviewOpened ?? 0).toBeGreaterThanOrEqual(1)
    expect(state.runtimeTelemetry?.thinkingPreviewClosed ?? 0).toBeGreaterThanOrEqual(1)
    expect(state.runtimeTelemetry?.thinkingPreviewExpired ?? 0).toBeGreaterThanOrEqual(1)

    const transcript = state.conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
    expect(transcript).not.toContain("[task tree] thinking")
  })
})
