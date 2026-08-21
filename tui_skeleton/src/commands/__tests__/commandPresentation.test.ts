import { afterEach, describe, expect, it, vi } from "vitest"

import { printCommandPresentation } from "../commandPresentation.js"

afterEach(() => {
  vi.restoreAllMocks()
})

describe("printCommandPresentation", () => {
  it("writes text presentations to stdout", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined)

    await printCommandPresentation({ mode: "text", jsonValue: null, text: "session complete" })

    expect(log).toHaveBeenCalledWith("session complete")
  })

  it("writes JSON presentations to stdout", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined)

    await printCommandPresentation({ mode: "json", jsonValue: { status: "completed" } })

    expect(log).toHaveBeenCalledWith(JSON.stringify({ status: "completed" }, null, 2))
  })
})
