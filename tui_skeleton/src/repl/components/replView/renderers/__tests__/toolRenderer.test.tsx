import React from "react"
import { describe, it, expect } from "vitest"
import { render } from "ink-testing-library"
import type { ToolLogEntry } from "../../../../types.js"
import { useToolRenderer } from "../toolRenderer.js"
import { stripAnsiCodes } from "../../utils/ansi.js"

const ToolEntryView = ({ entry }: { entry: ToolLogEntry }) => {
  const { renderToolEntry } = useToolRenderer({
    claudeChrome: false,
    verboseOutput: true,
    collapseThreshold: 100,
    collapseHead: 6,
    collapseTail: 6,
    labelWidth: 12,
    contentWidth: 40,
  })
  return <>{renderToolEntry(entry)}</>
}

describe("toolRenderer", () => {
  const renderWithWidth = (node: React.ReactElement, width: number) =>
    (render as unknown as (element: React.ReactElement, options?: { width?: number }) => { lastFrame: () => string | undefined })(
      node,
      { width },
    )
  it("clips tool detail lines instead of wrapping", () => {
    const long = "x".repeat(120)
    const entry: ToolLogEntry = {
      id: "tool-1",
      kind: "result",
      text: `ToolHeader\n${long}`,
      status: "success",
      createdAt: 0,
    }
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} />, 40)
    const output = lastFrame() ?? ""
    const lines = output.split(/\r?\n/).filter((line) => line.trim().length > 0)
    expect(lines.length).toBe(2)
    expect(lines[1]).not.toContain(long)
  })

  it("wraps tool header lines when needed", () => {
    const prevColumns = process.stdout.columns
    ;(process.stdout as unknown as { columns?: number }).columns = 30
    const longHeader = `ToolHeader(${"x".repeat(80)})`
    const entry: ToolLogEntry = {
      id: "tool-2",
      kind: "result",
      text: longHeader,
      status: "success",
      createdAt: 0,
    }
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} />, 30)
    const output = lastFrame() ?? ""
    expect(output).toContain("ToolHeader(")
    ;(process.stdout as unknown as { columns?: number }).columns = prevColumns
  })

  it("does not apply background to context diff lines", () => {
    const diffText = [
      "diff --git a/foo.txt b/foo.txt",
      "index 1234567..89abcde 100644",
      "--- a/foo.txt",
      "+++ b/foo.txt",
      "@@ -1,3 +1,3 @@",
      " context line",
      "-old line",
      "+new line",
    ].join("\n")
    const entry: ToolLogEntry = {
      id: "tool-diff",
      kind: "result",
      text: "Patch(foo.txt)",
      status: "success",
      createdAt: 0,
      display: {
        title: "Patch(foo.txt)",
        diff_blocks: [{ kind: "diff", unified: diffText }],
      },
    }
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} />, 80)
    const output = lastFrame() ?? ""
    const rawLines = output.split(/\r?\n/)
    const plainLines = rawLines.map((line) => stripAnsiCodes(line))
    const contextIndex = plainLines.findIndex((line) => line.includes("context line"))
    const addIndex = plainLines.findIndex((line) => line.includes("new line"))
    const contextLine = contextIndex >= 0 ? rawLines[contextIndex] ?? "" : ""
    const addLine = addIndex >= 0 ? rawLines[addIndex] ?? "" : ""
    expect(contextLine).not.toMatch(/\u001b\[48;/)
    expect(addLine).toMatch(/\u001b\[48;/)
  })
})
