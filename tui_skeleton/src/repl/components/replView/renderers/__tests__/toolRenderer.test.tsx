import React from "react"
import { describe, it, expect } from "vitest"
import { render } from "ink-testing-library"
import { Text } from "ink"
import type { ToolLogEntry } from "../../../../types.js"
import { useToolRenderer } from "../toolRenderer.js"
import { createToolRendererRegistry } from "../toolRendererRegistry.js"
import { stripAnsiCodes } from "../../utils/ansi.js"

const ToolEntryView = ({
  entry,
  registry,
}: {
  entry: ToolLogEntry
  registry?: ReturnType<typeof createToolRendererRegistry>
}) => {
  const { renderToolEntry } = useToolRenderer({
    claudeChrome: false,
    verboseOutput: true,
    collapseThreshold: 100,
    collapseHead: 6,
    collapseTail: 6,
    labelWidth: 12,
    contentWidth: 40,
    registry,
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

  it("formats [task] status rows without echoing the Task label token", () => {
    const entry: ToolLogEntry = {
      id: "tool-task",
      kind: "status",
      text: "[task] Task · running · Index workspace files · task-1",
      status: "pending",
      createdAt: 0,
    }
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} />, 100)
    const output = stripAnsiCodes(lastFrame() ?? "")
    expect(output).toMatch(/[☐-]\srunning · Index workspace files · task-1/)
    expect(output).not.toContain("Task · running")
    expect(output).not.toContain("… running")
    expect(output).not.toContain("● running")
  })

  it("renders completed [task] status rows with explicit success symbols", () => {
    const entry: ToolLogEntry = {
      id: "tool-task-complete",
      kind: "status",
      text: "[task] Task · completed · Compute TODO preview metrics · task-2",
      status: "success",
      createdAt: 0,
    }
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} />, 120)
    const output = stripAnsiCodes(lastFrame() ?? "")
    expect(output).toMatch(/[✔v]\scompleted · Compute TODO preview metrics · task-2/)
  })

  it("falls back to the legacy renderer when no custom handler matches", () => {
    const entry: ToolLogEntry = {
      id: "tool-fallback",
      kind: "result",
      text: "[status] fallback check",
      status: "success",
      createdAt: 0,
    }
    const registry = createToolRendererRegistry([
      {
        id: "never",
        match: (candidate) => candidate.id === "does-not-match",
        render: () => <>UNUSED</>,
      },
    ])
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} registry={registry} />, 80)
    const output = stripAnsiCodes(lastFrame() ?? "")
    expect(output).toContain("fallback check")
    expect(output).not.toContain("UNUSED")
  })

  it("supports custom registry render and measure overrides", () => {
    const entry: ToolLogEntry = {
      id: "tool-custom",
      kind: "result",
      text: "legacy-line",
      status: "success",
      createdAt: 0,
    }
    const registry = createToolRendererRegistry([
      {
        id: "custom",
        match: (candidate) => candidate.id === "tool-custom",
        render: () => <Text>CUSTOM RENDER</Text>,
        measure: () => 42,
      },
    ])
    const Test = () => {
      const { renderToolEntry, measureToolEntryLines } = useToolRenderer({
        claudeChrome: false,
        verboseOutput: true,
        collapseThreshold: 100,
        collapseHead: 6,
        collapseTail: 6,
        labelWidth: 12,
        contentWidth: 80,
        registry,
      })
      const measured = measureToolEntryLines(entry)
      return (
        <>
          <>{renderToolEntry(entry)}</>
          <Text>{`MEASURE:${measured}`}</Text>
        </>
      )
    }
    const { lastFrame } = renderWithWidth(<Test />, 80)
    const output = stripAnsiCodes(lastFrame() ?? "")
    expect(output).toContain("CUSTOM RENDER")
    expect(output).toContain("MEASURE:42")
    expect(output).not.toContain("legacy-line")
  })

  it("applies compact policy for partial output and expanded policy for completed output", () => {
    const longLines = Array.from({ length: 12 }, (_, index) => `line-${index + 1}`).join("\n")
    const policyProvider = {
      resolve: (entry: ToolLogEntry) => {
        if (entry.status === "pending") {
          return { mode: "compact" as const, collapseThreshold: 4, collapseHead: 1, collapseTail: 1 }
        }
        if (entry.status === "success") {
          return { mode: "expanded" as const, collapseThreshold: 4, collapseHead: 1, collapseTail: 1 }
        }
        return null
      },
    }
    const PartialView = () => {
      const { renderToolEntry } = useToolRenderer({
        claudeChrome: false,
        verboseOutput: false,
        collapseThreshold: 100,
        collapseHead: 6,
        collapseTail: 6,
        labelWidth: 12,
        contentWidth: 80,
        policyProvider,
      })
      return (
        <>
          {renderToolEntry(
            {
              id: "tool-partial",
              kind: "result",
              text: longLines,
              status: "pending",
              createdAt: 0,
            },
            "partial",
          )}
          {renderToolEntry(
            {
              id: "tool-complete",
              kind: "result",
              text: longLines,
              status: "success",
              createdAt: 0,
            },
            "complete",
          )}
        </>
      )
    }
    const { lastFrame } = renderWithWidth(<PartialView />, 80)
    const output = stripAnsiCodes(lastFrame() ?? "")
    expect(output).toContain("hidden (ctrl+o to expand)")
    expect(output).toContain("line-12")
  })

  it("renders artifact-backed tool output with preview and pointer metadata", () => {
    const entry: ToolLogEntry = {
      id: "tool-artifact",
      kind: "result",
      text: "Write(big.txt)",
      status: "success",
      createdAt: 0,
      display: {
        title: "Write(big.txt)",
        detail_artifact: {
          schema_version: "artifact_ref_v1",
          id: "artifact-deadbeef",
          kind: "tool_output",
          mime: "text/plain",
          size_bytes: 24000,
          sha256: "deadbeef",
          storage: "workspace_file",
          path: "docs_tmp/tui_tool_artifacts/tool_output/artifact-deadbeef.txt",
          preview: {
            lines: ["line-1", "line-2"],
            omitted_lines: 200,
            note: "Inline output truncated to artifact reference.",
          },
        },
      } as unknown as ToolLogEntry["display"],
    }
    const { lastFrame } = renderWithWidth(<ToolEntryView entry={entry} />, 120)
    const output = stripAnsiCodes(lastFrame() ?? "")
    expect(output).toContain("artifact")
    expect(output).toContain("artifact-deadbeef.txt")
    expect(output).toContain("line-1")
    expect(output).toContain("note: Inline output truncated to artifact reference.")
  })
})
