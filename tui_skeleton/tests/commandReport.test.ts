import { describe, expect, it } from "vitest"
import { renderOptionalReportLine, renderReportDocument } from "../src/commands/commandReport.js"

describe("commandReport", () => {
  it("renders optional report lines only when populated", () => {
    expect(renderOptionalReportLine("Base URL", "http://x")).toBe("Base URL: http://x")
    expect(renderOptionalReportLine("Base URL", "")).toBeNull()
  })

  it("renders report documents with lines and sections", () => {
    expect(
      renderReportDocument("Doctor", {
        lines: ["Base URL: http://x"],
        sections: [{ title: "Conversation", lines: ["a", "b"] }],
      }),
    ).toBe("Doctor\n\nBase URL: http://x\n\n=== Conversation ===\na\nb")
  })
})
