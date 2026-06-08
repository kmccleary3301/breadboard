import { describe, expect, it } from "vitest"

import {
  createScrollbackSafeInkStdout,
  normalizeScrollbackSafeInkWrite,
  resolveStdoutRowDiagnostics,
} from "../src/repl/inkScrollbackStdout.js"

describe("createScrollbackSafeInkStdout", () => {
  it("preserves columns but advertises large rows to bypass Ink clearTerminal overflow", () => {
    const writes: string[] = []
    const stdout = {
      rows: 24,
      columns: 88,
      write(value: string) {
        writes.push(value)
        return true
      },
    } as unknown as NodeJS.WriteStream

    const wrapped = createScrollbackSafeInkStdout(stdout, true)

    expect(wrapped.rows).toBe(100_000)
    expect(wrapped.columns).toBe(88)
    expect(wrapped.write("ok")).toBe(true)
    expect(writes).toEqual(["ok"])
  })

  it("strips Ink clearTerminal scrollback erasure in scrollback-safe mode", () => {
    const writes: string[] = []
    const stdout = {
      write(value: string) {
        writes.push(value)
        return true
      },
    } as unknown as NodeJS.WriteStream

    const wrapped = createScrollbackSafeInkStdout(stdout, true)
    wrapped.write("\x1b[2J\x1b[3J\x1b[Hpayload")

    expect(writes).toEqual(["\r\x1b[Jpayload"])
  })

  it("normalizes destructive clear sequences without changing ordinary cursor writes", () => {
    expect(normalizeScrollbackSafeInkWrite("a\x1b[2J\x1b[3J\x1b[Hb")).toBe("a\r\x1b[Jb")
    expect(normalizeScrollbackSafeInkWrite("a\x1b[3Jb")).toBe("a\r\x1b[Jb")
    expect(normalizeScrollbackSafeInkWrite("a\x1bcb")).toBe("a\r\x1b[Jb")
    expect(normalizeScrollbackSafeInkWrite("\x1b[2Kok")).toBe("\x1b[2Kok")
  })

  it("returns stdout unchanged when disabled", () => {
    const stdout = { rows: 12 } as unknown as NodeJS.WriteStream
    expect(createScrollbackSafeInkStdout(stdout, false)).toBe(stdout)
  })

  it("reports whether row layout came from actual rows, fallback rows, or default rows", () => {
    expect(resolveStdoutRowDiagnostics(100_000, 24)).toEqual({
      actualRows: 24,
      fallbackRows: 100_000,
      resolvedRows: 24,
      source: "actual",
    })
    expect(resolveStdoutRowDiagnostics(31, null)).toEqual({
      actualRows: null,
      fallbackRows: 31,
      resolvedRows: 31,
      source: "fallback",
    })
    expect(resolveStdoutRowDiagnostics(null, null)).toEqual({
      actualRows: null,
      fallbackRows: null,
      resolvedRows: 40,
      source: "default",
    })
  })
})
