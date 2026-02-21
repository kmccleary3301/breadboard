import { describe, expect, it } from "vitest"
import { formatTodoModalRowLabel, todoCheckboxToken } from "../todoCheckbox.js"
import { ASCII_ONLY } from "../../theme.js"

describe("todoCheckboxToken", () => {
  it("renders completed statuses as checked", () => {
    const expected = ASCII_ONLY ? "[x]" : "🗹"
    expect(todoCheckboxToken("done")).toBe(expected)
    expect(todoCheckboxToken("completed")).toBe(expected)
    expect(todoCheckboxToken("complete")).toBe(expected)
  })

  it("renders open statuses as unchecked", () => {
    const expected = ASCII_ONLY ? "[ ]" : "☐"
    expect(todoCheckboxToken("todo")).toBe(expected)
    expect(todoCheckboxToken("in_progress")).toBe(expected)
    expect(todoCheckboxToken(undefined)).toBe(expected)
  })

  it("renders blocked/failed statuses with a failure marker", () => {
    const expected = ASCII_ONLY ? "[!]" : "☒"
    expect(todoCheckboxToken("blocked")).toBe(expected)
    expect(todoCheckboxToken("failed")).toBe(expected)
    expect(todoCheckboxToken("canceled")).toBe(expected)
    expect(todoCheckboxToken("cancelled")).toBe(expected)
  })
})

describe("formatTodoModalRowLabel", () => {
  it("prefixes todo item labels with checkbox markers", () => {
    const doneToken = ASCII_ONLY ? "[x]" : "🗹"
    const openToken = ASCII_ONLY ? "[ ]" : "☐"
    expect(formatTodoModalRowLabel("Ship Phase 4 closeout", "done")).toBe(`  ${doneToken} Ship Phase 4 closeout`)
    expect(formatTodoModalRowLabel("Implement replay mode", "todo")).toBe(`  ${openToken} Implement replay mode`)
  })
})
