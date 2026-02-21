import { describe, expect, it } from "vitest"
import { ALT_BUFFER_ENTER, ALT_BUFFER_EXIT, createAltBufferSession } from "../altBufferSession.js"

describe("altBufferSession", () => {
  it("emits enter/exit exactly once per transition", () => {
    const writes: string[] = []
    const session = createAltBufferSession((chunk) => writes.push(chunk), true)
    session.sync(true)
    session.sync(true)
    session.sync(false)
    session.sync(false)
    expect(writes).toEqual([ALT_BUFFER_ENTER, ALT_BUFFER_EXIT])
  })

  it("reset exits active session and is idempotent", () => {
    const writes: string[] = []
    const session = createAltBufferSession((chunk) => writes.push(chunk), true)
    session.sync(true)
    session.reset()
    session.reset()
    expect(session.isActive()).toBe(false)
    expect(writes).toEqual([ALT_BUFFER_ENTER, ALT_BUFFER_EXIT])
  })

  it("falls back safely when disabled", () => {
    const writes: string[] = []
    const session = createAltBufferSession((chunk) => writes.push(chunk), false)
    session.sync(true)
    session.reset()
    expect(session.isActive()).toBe(false)
    expect(writes).toEqual([])
  })
})

