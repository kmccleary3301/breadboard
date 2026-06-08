import { describe, expect, it } from "vitest"

import { buildSuggestions } from "../slashCommands.js"

describe("slash command continuation defaults", () => {
  it("prioritizes continuation-focused suggestions for bare slash", () => {
    expect(buildSuggestions("/", 5).map((row) => row.command)).toEqual([
      "/resume",
      "/transcript",
      "/attach",
      "/models",
      "/shortcuts",
    ])
  })

  it("still returns fuzzy matches for targeted queries", () => {
    expect(buildSuggestions("/tr", 5).map((row) => row.command)).toContain("/transcript")
    expect(buildSuggestions("/sho", 5).map((row) => row.command)).toContain("/shortcuts")
  })

  it("hides suggestions for exact slash commands so plain Enter submits the typed command", () => {
    expect(buildSuggestions("/models", 5)).toEqual([])
    expect(buildSuggestions("/files", 5)).toEqual([])
  })

  it("includes disabled reasons for context-gated command rows", () => {
    expect(buildSuggestions("/sto", 5, { pendingResponse: false })[0]).toMatchObject({
      command: "/stop",
      availability: "context-gated",
      disabledReason: "No running response to stop.",
    })
    expect(buildSuggestions("/sto", 5, { pendingResponse: true })[0]).toMatchObject({
      command: "/stop",
      availability: "available",
    })
  })
})
