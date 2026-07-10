import { describe, expect, it } from "vitest"

import { resolveSlashEnterDecision } from "../src/repl/components/replView/controller/keyHandlers/useEditorKeys.ts"

describe("slash Enter authority", () => {
  it("executes the highlighted slash suggestion for partial slash input", () => {
    expect(
      resolveSlashEnterDecision({
        input: "/mo",
        suggestions: [{ command: "/mode" }, { command: "/models" }],
        suggestIndex: 1,
        hasSubmitSuggestedSlashCommand: true,
      }),
    ).toEqual({ kind: "submit-suggestion", command: "/models" })
  })

  it("does not submit arbitrary prompt text while a suggestion owns Enter", () => {
    expect(
      resolveSlashEnterDecision({
        input: "plain",
        suggestions: [{ command: "/models" }],
        suggestIndex: 0,
        hasSubmitSuggestedSlashCommand: true,
      }),
    ).toEqual({ kind: "apply-suggestion", index: 0 })
  })

  it("allows exact slash commands to submit when suggestions are not active", () => {
    expect(
      resolveSlashEnterDecision({
        input: "/models",
        suggestions: [],
        suggestIndex: 0,
        hasSubmitSuggestedSlashCommand: true,
      }),
    ).toEqual({ kind: "none" })
  })

  it("does not submit disabled slash suggestions", () => {
    expect(
      resolveSlashEnterDecision({
        input: "/sto",
        suggestions: [{ command: "/stop", availability: "context-gated" }],
        suggestIndex: 0,
        hasSubmitSuggestedSlashCommand: true,
      }),
    ).toEqual({ kind: "disabled-suggestion" })
  })
})
