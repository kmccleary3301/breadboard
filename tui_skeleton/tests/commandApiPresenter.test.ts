import { describe, expect, it } from "vitest"
import { resolveCommandResultEnvelope } from "../src/commands/commandResultEnvelope.js"
import {
  renderDetailCommandResultText,
  renderReportCommandResultText,
} from "../src/commands/commandApiPresenter.js"

describe("commandApiPresenter", () => {
  it("renders report-style command text", () => {
    expect(
      renderReportCommandResultText({
        title: "Config",
        lines: ["Base URL: http://localhost"],
        sections: [{ title: "Warnings", lines: ["- drift"] }],
      }),
    ).toContain("Warnings")
  })

  it("renders detail-style command text", () => {
    expect(
      renderDetailCommandResultText({
        title: "Render",
        lines: ["Session: abc"],
        sections: [{ title: "Conversation", lines: ["USER: hi"] }],
        trailingLines: ["Events received: 1"],
      }),
    ).toContain("Events received: 1")
  })

  it("feeds report text cleanly into result envelopes", () => {
    const text = renderReportCommandResultText({
      title: "Config",
      lines: ["ok"],
    })
    expect(
      resolveCommandResultEnvelope({
        mode: "summary",
        envelope: { jsonValue: { ok: true }, text },
      }),
    ).toEqual({ kind: "text", value: text })
  })

  it("feeds detail text cleanly into result envelopes", () => {
    const text = renderDetailCommandResultText({
      title: "Render",
      lines: ["ok"],
    })
    expect(
      resolveCommandResultEnvelope({
        mode: "json",
        envelope: { jsonValue: { ok: true }, text },
      }),
    ).toEqual({ kind: "json", value: JSON.stringify({ ok: true }, null, 2) })
  })
})
