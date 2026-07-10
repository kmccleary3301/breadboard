import { describe, expect, it } from "vitest"
import { SLASH_COMMAND_REGISTRY } from "../src/repl/slashCommandRegistry.js"
import { buildSlashCommandMarkdown, buildSlashCommandReport } from "../tools/reports/slashCommandReport.js"

describe("slash command report", () => {
  it("exports every registry command with schema metadata", () => {
    const report = buildSlashCommandReport()
    expect(report.commandCount).toBe(SLASH_COMMAND_REGISTRY.length)
    expect(report.commands.map((command) => command.name)).toEqual(SLASH_COMMAND_REGISTRY.map((command) => command.name))
    expect(report.commands.find((command) => command.name === "mode")?.argumentSchema?.[0]).toMatchObject({
      kind: "enum",
      values: ["plan", "build", "auto"],
    })
  })

  it("renders markdown from registry data", () => {
    const markdown = buildSlashCommandMarkdown()
    expect(markdown).toContain("# BreadBoard Slash Command Registry")
    expect(markdown).toContain("### /mode <plan|build|auto>")
    expect(markdown).toContain("values: plan|build|auto")
    expect(markdown).toContain("### /debug-config")
  })
})
