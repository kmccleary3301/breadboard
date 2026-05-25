import React from "react"
import { render } from "ink-testing-library"
import { describe, expect, it } from "vitest"
import { TranscriptViewer } from "../TranscriptViewer.js"

describe("TranscriptViewer", () => {
  it("renders empty search mode when search is open before a query is typed", () => {
    const { lastFrame } = render(
      <TranscriptViewer
        lines={["one", "two"]}
        cols={80}
        rows={10}
        scroll={0}
        searchOpen
        searchQuery=""
        matchCount={0}
      />,
    )

    const frame = lastFrame() ?? ""
    expect(frame).toContain("Search:")
    expect(frame).not.toContain("Press / to search")
  })

  it("uses an explicit title label for alternate viewer modes", () => {
    const { lastFrame } = render(
      <TranscriptViewer
        lines={['Status [raw] {"ok":true}']}
        cols={80}
        rows={10}
        scroll={0}
        titleLabel="raw event viewer"
      />,
    )

    const frame = lastFrame() ?? ""
    expect(frame).toContain("breadboard raw event viewer")
    expect(frame).not.toContain("breadboard transcript viewer")
  })
})
