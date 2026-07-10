import { describe, expect, it, vi } from "vitest"

import { BRACKET_END, BRACKET_START, createPasteTracker, processInputChunk } from "../src/repl/inputChunkProcessor.ts"

const makeCallbacks = () => ({
  inserted: [] as string[],
  pasted: [] as string[],
  submit: vi.fn(),
  deleteWordBackward: vi.fn(),
  deleteBackward: vi.fn(),
})

describe("inputChunkProcessor bracketed paste", () => {
  it("delivers bracketed paste payload without inserting terminal markers", () => {
    const tracker = createPasteTracker()
    const calls = makeCallbacks()
    const handled = processInputChunk(`${BRACKET_START}line one\nline two${BRACKET_END}`, tracker, { ctrl: false, meta: false }, {
      insertText: (value) => calls.inserted.push(value),
      handlePastePayload: (value) => calls.pasted.push(value),
      submit: calls.submit,
      deleteWordBackward: calls.deleteWordBackward,
      deleteBackward: calls.deleteBackward,
    })

    expect(handled).toBe(true)
    expect(calls.pasted).toEqual(["line one\nline two"])
    expect(calls.inserted.join("")).not.toContain("[200~")
    expect(calls.inserted.join("")).not.toContain("[201~")
    expect(calls.submit).not.toHaveBeenCalled()
  })

  it("reconstructs bracketed paste markers split across chunks", () => {
    const tracker = createPasteTracker()
    const calls = makeCallbacks()
    const callbacks = {
      insertText: (value: string) => calls.inserted.push(value),
      handlePastePayload: (value: string) => calls.pasted.push(value),
      submit: calls.submit,
      deleteWordBackward: calls.deleteWordBackward,
      deleteBackward: calls.deleteBackward,
    }

    expect(processInputChunk("\u001b[20", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)
    expect(processInputChunk("0~payload", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)
    expect(processInputChunk("\u001b[201", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)
    expect(processInputChunk("~", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)

    expect(calls.pasted).toEqual(["payload"])
    expect(calls.inserted.join("")).toBe("")
  })

  it("delivers ESC-stripped bracketed paste payload without visible marker leakage", () => {
    const tracker = createPasteTracker()
    const calls = makeCallbacks()
    const handled = processInputChunk("[200~visible stripped payload[201~", tracker, { ctrl: false, meta: false }, {
      insertText: (value) => calls.inserted.push(value),
      handlePastePayload: (value) => calls.pasted.push(value),
      submit: calls.submit,
      deleteWordBackward: calls.deleteWordBackward,
      deleteBackward: calls.deleteBackward,
    })

    expect(handled).toBe(true)
    expect(calls.pasted).toEqual(["visible stripped payload"])
    expect(calls.inserted.join("")).toBe("")
  })

  it("reconstructs ESC-stripped bracketed paste markers split across chunks", () => {
    const tracker = createPasteTracker()
    const calls = makeCallbacks()
    const callbacks = {
      insertText: (value: string) => calls.inserted.push(value),
      handlePastePayload: (value: string) => calls.pasted.push(value),
      submit: calls.submit,
      deleteWordBackward: calls.deleteWordBackward,
      deleteBackward: calls.deleteBackward,
    }

    expect(processInputChunk("[20", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)
    expect(processInputChunk("0~payload", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)
    expect(processInputChunk("[201", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)
    expect(processInputChunk("~", tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)

    expect(calls.pasted).toEqual(["payload"])
    expect(calls.inserted.join("")).toBe("")
  })

  it("submits only a trailing newline outside bracketed paste", () => {
    const tracker = createPasteTracker()
    const calls = makeCallbacks()
    const callbacks = {
      insertText: (value: string) => calls.inserted.push(value),
      handlePastePayload: (value: string) => calls.pasted.push(value),
      submit: calls.submit,
      deleteWordBackward: calls.deleteWordBackward,
      deleteBackward: calls.deleteBackward,
    }

    expect(processInputChunk(`${BRACKET_START}a\nb\n${BRACKET_END}\n`, tracker, { ctrl: false, meta: false }, callbacks)).toBe(true)

    expect(calls.pasted).toEqual(["a\nb\n"])
    expect(calls.submit).toHaveBeenCalledTimes(1)
  })
})
