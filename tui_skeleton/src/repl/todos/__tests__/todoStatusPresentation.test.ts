import { describe, expect, it } from "vitest"
import { NEUTRAL_COLORS, SEMANTIC_COLORS } from "../../designSystem.js"
import { todoStatusPresentation } from "../todoStatusPresentation.js"

describe("todoStatusPresentation", () => {
  it("defines the complete presentation contract for every known status", () => {
    expect(todoStatusPresentation("todo")).toEqual({
      label: "Todo",
      previewSymbol: { ascii: "[ ]", unicode: "☐" },
      textMark: " ",
      previewColor: NEUTRAL_COLORS.nearWhite,
      panelColor: SEMANTIC_COLORS.warning,
    })
    expect(todoStatusPresentation("in_progress")).toEqual({
      label: "In Progress",
      previewSymbol: { ascii: "[ ]", unicode: "☐" },
      textMark: "~",
      previewColor: SEMANTIC_COLORS.warning,
      panelColor: SEMANTIC_COLORS.info,
    })
    expect(todoStatusPresentation("done")).toEqual({
      label: "Done",
      previewSymbol: { ascii: "[x]", unicode: "🗹" },
      textMark: "x",
      previewColor: "dim",
      panelColor: SEMANTIC_COLORS.success,
    })
    expect(todoStatusPresentation("blocked")).toEqual({
      label: "Blocked",
      previewSymbol: { ascii: "[!]", unicode: "☒" },
      textMark: "!",
      previewColor: SEMANTIC_COLORS.error,
      panelColor: SEMANTIC_COLORS.error,
    })
    expect(todoStatusPresentation("canceled")).toEqual({
      label: "Canceled",
      previewSymbol: { ascii: "[!]", unicode: "☒" },
      textMark: "-",
      previewColor: NEUTRAL_COLORS.dimGray,
      panelColor: NEUTRAL_COLORS.midGray,
    })
  })

  it("normalizes every supported engine alias through the shared presentation", () => {
    expect(todoStatusPresentation("progress")).toBe(todoStatusPresentation("in_progress"))
    expect(todoStatusPresentation("active")).toBe(todoStatusPresentation("in_progress"))
    expect(todoStatusPresentation("complete")).toBe(todoStatusPresentation("done"))
    expect(todoStatusPresentation("completed")).toBe(todoStatusPresentation("done"))
    expect(todoStatusPresentation("failed")).toBe(todoStatusPresentation("blocked"))
    expect(todoStatusPresentation("cancelled")).toBe(todoStatusPresentation("canceled"))
    expect(todoStatusPresentation("pending")).toBe(todoStatusPresentation("todo"))
  })

  it("preserves the open-todo presentation for unknown and absent statuses", () => {
    const fallback = todoStatusPresentation("unknown-engine-status")
    expect(fallback).toEqual({
      label: "Todo",
      previewSymbol: { ascii: "[ ]", unicode: "☐" },
      textMark: " ",
      previewColor: NEUTRAL_COLORS.nearWhite,
      panelColor: SEMANTIC_COLORS.warning,
    })
    expect(todoStatusPresentation(undefined)).toBe(fallback)
    expect(todoStatusPresentation("DONE")).toBe(todoStatusPresentation("done"))
  })
})
