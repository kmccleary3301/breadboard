import { afterEach, describe, expect, it, vi } from "vitest"

import { subscribeTerminalSize } from "../src/repl/hooks/useTerminalSize.js"

describe("subscribeTerminalSize", () => {
  const unsubscribers: Array<() => void> = []

  afterEach(() => {
    while (unsubscribers.length > 0) {
      unsubscribers.pop()?.()
    }
    vi.restoreAllMocks()
  })

  it("uses one process/stdout listener for many terminal-size consumers", () => {
    const stdoutOn = vi.spyOn(process.stdout, "on")
    const stdoutOff = vi.spyOn(process.stdout, "off")
    const processOn = vi.spyOn(process, "on")
    const processOff = vi.spyOn(process, "off")

    const first = subscribeTerminalSize(() => undefined)
    unsubscribers.push(first)
    const second = subscribeTerminalSize(() => undefined)
    unsubscribers.push(second)

    expect(stdoutOn.mock.calls.filter(([event]) => event === "resize")).toHaveLength(1)
    expect(processOn.mock.calls.filter(([event]) => event === "SIGWINCH")).toHaveLength(1)

    first()
    unsubscribers.pop()
    expect(stdoutOff.mock.calls.filter(([event]) => event === "resize")).toHaveLength(0)
    expect(processOff.mock.calls.filter(([event]) => event === "SIGWINCH")).toHaveLength(0)

    second()
    unsubscribers.pop()
    expect(stdoutOff.mock.calls.filter(([event]) => event === "resize")).toHaveLength(1)
    expect(processOff.mock.calls.filter(([event]) => event === "SIGWINCH")).toHaveLength(1)
  })
})
