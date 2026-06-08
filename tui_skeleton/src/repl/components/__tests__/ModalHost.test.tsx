import { describe, expect, it } from "vitest"
import { render } from "ink-testing-library"
import React from "react"
import { Text } from "ink"
import { ModalHost } from "../ModalHost.js"

describe("ModalHost", () => {
  it("keeps centered overlays bounded in-flow instead of materializing a terminal-height takeover", () => {
    const { lastFrame } = render(
      <ModalHost
        stack={[
          {
            id: "palette",
            render: () => <Text>COMMAND PALETTE</Text>,
          },
        ]}
      >
        <Text>BASE CONTENT</Text>
      </ModalHost>,
    )

    const frame = lastFrame() ?? ""
    expect(frame).toContain("BASE CONTENT")
    expect(frame).toContain("COMMAND PALETTE")
    expect(frame.split("\n").length).toBeLessThan(10)
  })

  it("renders sheet overlays directly after the active viewport content", () => {
    const { lastFrame } = render(
      <ModalHost
        stack={[
          {
            id: "sheet",
            layout: "sheet",
            render: () => <Text>SHEET CONTENT</Text>,
          },
        ]}
      >
        <Text>BASE CONTENT</Text>
      </ModalHost>,
    )

    const frame = lastFrame() ?? ""
    const baseIndex = frame.indexOf("BASE CONTENT")
    const sheetIndex = frame.indexOf("SHEET CONTENT")
    expect(baseIndex).toBeGreaterThanOrEqual(0)
    expect(sheetIndex).toBeGreaterThan(baseIndex)
  })

  it("clears a sheet overlay cleanly when the stack closes", () => {
    const view = render(
      <ModalHost
        stack={[
          {
            id: "sheet",
            layout: "sheet",
            render: () => <Text>SHEET CONTENT</Text>,
          },
        ]}
      >
        <Text>BASE CONTENT</Text>
      </ModalHost>,
    )

    view.rerender(
      <ModalHost stack={[]}>
        <Text>BASE CONTENT</Text>
      </ModalHost>,
    )

    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("BASE CONTENT")
    expect(frame).not.toContain("SHEET CONTENT")
  })

  it("clears a centered overlay cleanly when the stack closes", () => {
    const view = render(
      <ModalHost
        stack={[
          {
            id: "palette",
            render: () => <Text>COMMAND PALETTE</Text>,
          },
        ]}
      >
        <Text>BASE CONTENT</Text>
      </ModalHost>,
    )

    view.rerender(
      <ModalHost stack={[]}>
        <Text>BASE CONTENT</Text>
      </ModalHost>,
    )

    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("BASE CONTENT")
    expect(frame).not.toContain("COMMAND PALETTE")
  })

})
