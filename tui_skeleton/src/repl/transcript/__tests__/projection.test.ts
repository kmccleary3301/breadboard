import { describe, expect, it } from "vitest"
import type { TranscriptCellRole, TranscriptItem } from "../../transcriptModel.js"
import { buildTranscriptExportLines, projectTranscriptItem } from "../projection.js"

const now = 1

const message = (speaker: "user" | "assistant" | "system", text: string, role?: TranscriptCellRole): TranscriptItem => ({
  id: `msg-${role ?? speaker}`,
  kind: "message",
  speaker,
  text,
  phase: "final",
  createdAt: now,
  source: "conversation",
  ...(role ? { cellRole: role } : {}),
})

const tool = (role: TranscriptCellRole, text: string): TranscriptItem => ({
  id: `tool-${role}`,
  kind: "tool",
  toolKind: role === "tool-error" ? "error" : role === "tool-call" ? "call" : "result",
  text,
  status: role === "tool-error" ? "error" : role === "tool-call" ? "pending" : "success",
  createdAt: now,
  source: "tool",
  cellRole: role,
})

const system = (role: TranscriptCellRole, text: string): TranscriptItem => ({
  id: `sys-${role}`,
  kind: "system",
  systemKind: role === "command-result" ? "command-result" : "notice",
  text,
  createdAt: now,
  source: "system",
  cellRole: role,
})

const cases: Array<{ role: TranscriptCellRole; item: TranscriptItem; inspectable: boolean }> = [
  { role: "user-request", item: message("user", "user asks for work"), inspectable: false },
  { role: "assistant-message", item: message("assistant", "assistant answers"), inspectable: false },
  { role: "tool-summary", item: tool("tool-summary", "read files summary"), inspectable: false },
  { role: "tool-call", item: tool("tool-call", "Shell(make test)"), inspectable: true },
  { role: "tool-result", item: tool("tool-result", "42 passed"), inspectable: true },
  { role: "tool-error", item: tool("tool-error", "compile error"), inspectable: true },
  { role: "diff", item: tool("diff", "src/a.ts +4 -1"), inspectable: true },
  { role: "approval", item: system("approval", "Permission required"), inspectable: true },
  { role: "interrupted", item: system("interrupted", "Interrupted by user"), inspectable: false },
  { role: "status", item: system("status", "Ready"), inspectable: false },
  { role: "landing", item: system("landing", "BreadBoard workspace"), inspectable: false },
  { role: "command-result", item: system("command-result", "Transcript saved"), inspectable: true },
  { role: "system", item: message("system", "system notice", "system"), inspectable: false },
]

describe("projectTranscriptItem", () => {
  it.each(cases)("projects $role across rich viewer raw and export modes", ({ role, item, inspectable }) => {
    const projection = projectTranscriptItem(item)
    expect(projection.role).toBe(role)
    expect(projection.richSummaryLines.length).toBeGreaterThan(0)
    expect(projection.viewerLines.length).toBeGreaterThan(0)
    expect(projection.rawLines.join("\n")).toContain(`[${role}]`)
    expect(projection.exportLines.join("\n").toLowerCase()).toContain(role.replaceAll("-", " "))
    expect(projection.widthPolicy).toMatch(/^(rewrap|truncate|preserve|detail-only)$/)
    expect(projection.inspectable).toBe(inspectable)
  })

  it("preserves multiline content in viewer raw and export projections", () => {
    const projection = projectTranscriptItem(message("assistant", "line one\nline two"))
    expect(projection.viewerLines).toEqual(["● line one", "  line two"])
    expect(projection.rawLines).toContain("line two")
    expect(projection.exportLines).toContain("line two")
  })

  it("builds a role-labeled transcript export from the projection registry", () => {
    const lines = buildTranscriptExportLines([
      message("user", "please inspect"),
      tool("tool-result", "Read(package.json)"),
      message("assistant", "inspection complete"),
    ])
    const body = lines.join("\n")
    expect(body).toContain("## user request")
    expect(body).toContain("please inspect")
    expect(body).toContain("## tool result")
    expect(body).toContain("Read(package.json)")
    expect(body).toContain("## assistant message")
    expect(body).toContain("inspection complete")
  })
})
