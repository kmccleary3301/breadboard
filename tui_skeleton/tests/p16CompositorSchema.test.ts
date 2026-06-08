import { describe, expect, it } from "vitest"
import { requiredCompositorArtifacts, validateP16CompositorScene } from "../tools/p16-compositor/schema.js"

const validScene = {
  schemaVersion: 1,
  id: "valid_p16_scene",
  title: "Valid P16 Scene",
  expectedOutcome: "pass",
  claimScope: "production-renderer-compositor",
  productionEquivalence: {
    required: true,
    renderer: "ReplView",
    allowedSyntheticLayers: ["model", "tool", "workspace"],
    forbiddenBypass: ["renderer", "transcript-projection", "composer-footer", "modal-picker", "taskboard-state", "direct-cell-mutation"],
  },
  surfaces: ["transcript"],
  sizes: [{ cols: 72, rows: 22 }],
  timeline: [{ kind: "user", text: "hello" }],
  expectedMarkers: ["hello"],
  invariants: ["COMPOSITOR-PRODUCTION-RENDERER", "COMPOSITOR-NO-FAKE-RENDERER", "COMPOSITOR-ARTIFACT-PACKET-COMPLETE"],
  artifacts: { required: requiredCompositorArtifacts() },
  claimBoundary: "bounded compositor evidence only",
}

describe("P16 compositor scene schema", () => {
  it("accepts a production ReplView compositor scene", () => {
    expect(validateP16CompositorScene(validScene).ok).toBe(true)
  })

  it("rejects fake renderer claims", () => {
    const result = validateP16CompositorScene({
      ...validScene,
      productionEquivalence: { ...validScene.productionEquivalence, renderer: "FakeRenderer" },
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("renderer must be ReplView")
  })

  it("rejects missing forbidden bypass declarations", () => {
    const result = validateP16CompositorScene({
      ...validScene,
      productionEquivalence: { ...validScene.productionEquivalence, forbiddenBypass: ["renderer"] },
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("forbiddenBypass must include transcript-projection")
  })

  it("rejects incomplete artifact packets", () => {
    const result = validateP16CompositorScene({ ...validScene, artifacts: { required: ["manifest.json"] } })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("artifacts.required missing command.txt")
  })

  it("requires expected-red scenes to declare knownBadKind", () => {
    const result = validateP16CompositorScene({ ...validScene, expectedOutcome: "fail" })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("known-bad scenes")
  })
})
