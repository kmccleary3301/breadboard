import { describe, expect, it } from "vitest"
import path from "node:path"
import { readP16CompositorScene } from "../tools/p16-compositor/schema.js"
import { evaluateInvariants, reduceScene } from "../tools/p16-compositor/render.js"

const scenePath = (name: string) => path.resolve("scenarios", "p16", "compositor", name)

describe("P16 compositor reducer and known-bad calibration", () => {
  it("reduces duplicate-prompt known-bad into an invariant failure", async () => {
    const scene = await readP16CompositorScene(scenePath("known_bad_duplicate_prompt.json"))
    const state = reduceScene(scene)
    const results = evaluateInvariants(scene, state, [{ cols: 72, rows: 22, frame: "", plain: "Duplicate me once.", maxVisibleWidth: 18, lineCount: 1 }], scene.artifacts.required)
    expect(results.some((result) => result.id === "COMPOSITOR-PROMPT-EXACTLY-ONCE" && result.status === "fail")).toBe(true)
  })

  it("reduces ready-lie recovery known-bad into an invariant failure", async () => {
    const scene = await readP16CompositorScene(scenePath("known_bad_ready_lie_recovery.json"))
    const state = reduceScene(scene)
    const results = evaluateInvariants(scene, state, [{ cols: 72, rows: 22, frame: "", plain: "Ready", maxVisibleWidth: 5, lineCount: 1 }], scene.artifacts.required)
    expect(results.some((result) => result.id === "COMPOSITOR-NO-READY-LIE" && result.status === "fail")).toBe(true)
  })
})
