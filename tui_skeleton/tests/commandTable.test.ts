import { describe, expect, it } from "vitest"
import { renderSimpleTable } from "../src/commands/commandTable.js"

describe("commandTable", () => {
  it("renders a simple dash-separated table", () => {
    expect(
      renderSimpleTable(["Type", "Path"], [["file", "src/app.ts"]]),
    ).toBe(["Type  Path      ", "----  ----------", "file  src/app.ts"].join("\n"))
  })

  it("supports alternate separators and trimmed rows", () => {
    expect(
      renderSimpleTable(["A", "B"], [["1", "22"]], {
        separatorChar: "─",
        trimTrailingWhitespace: true,
      }),
    ).toBe(["A  B", "─  ──", "1  22"].join("\n"))
  })
})
