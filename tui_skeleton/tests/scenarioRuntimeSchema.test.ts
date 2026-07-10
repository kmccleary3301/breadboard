import { describe, expect, it } from "vitest"
import { validateScenario } from "../tools/scenario-runtime/schema"

const baseScenario = {
  schemaVersion: 1,
  id: "valid_scenario",
  claimScope: "pty",
  productionEquivalence: {
    required: true,
    allowedSyntheticLayers: ["model", "workspace"],
    forbiddenBypass: ["renderer"],
  },
  environment: {},
  launch: { engineMode: "test-owned" },
  terminal: { lanes: ["pty"], initial: { cols: 100, rows: 30 } },
  timeline: [{ kind: "checkpoint", id: "final" }],
  invariants: [{ id: "GLOBAL-NO-RAW-ANSI", severity: "blocker" }],
  artifacts: { required: ["manifest", "raw", "grid", "state", "invariant-report"] },
}

describe("scenario runtime schema", () => {
  it("accepts a valid production-equivalent scenario", () => {
    expect(validateScenario(baseScenario).ok).toBe(true)
  })

  it("rejects real-terminal claims that only run PTY", () => {
    const result = validateScenario({ ...baseScenario, claimScope: "real-terminal" })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("real-terminal claim")
  })

  it("rejects resize invariants without resize steps", () => {
    const result = validateScenario({
      ...baseScenario,
      invariants: [{ id: "RESIZE-ACTION-OBSERVED", severity: "blocker" }],
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("resize invariant")
  })

  it("rejects fault steps without owned engine mode", () => {
    const result = validateScenario({
      ...baseScenario,
      launch: { engineMode: "external" },
      timeline: [{ kind: "fault", event: { kind: "engine-death", phase: "idle" } }],
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("fault steps require")
  })

  it("accepts V6 campaign metadata and expected-red declarations", () => {
    const result = validateScenario({
      ...baseScenario,
      family: "scrollback",
      campaign: "p14-v6",
      knownFailureClass: "rendering.scrollback.host_history_lost",
      expectedOutcome: { verdict: "fail", failureClass: "rendering.scrollback.host_history_lost" },
      actionRequirements: { modalOpened: "optional", focusReturned: "observation-only" },
      terminalMatrix: { pty: { expectedOutcome: "fail" } },
      performanceBudget: { maxDurationMs: 10000, maxRawBytes: 1000000 },
      visualEvidence: { screenshots: "optional", textExport: "required" },
      transcriptExpectation: { promptCardinality: "exactly-once" },
      hotRegionExpectation: { maxRows: 8 },
      mutationProfile: { seed: 42, dimensions: ["resize"] },
      repeat: { count: 2 },
    })
    expect(result.ok).toBe(true)
  })

  it("rejects invalid V6 expected outcome and action requirements", () => {
    const result = validateScenario({
      ...baseScenario,
      expectedOutcome: { verdict: "maybe" },
      actionRequirements: { modalOpened: "sometimes" },
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join("\n")).toContain("expectedOutcome.verdict")
    expect(result.errors.join("\n")).toContain("actionRequirements.modalOpened")
  })
})
