import { describe, it, expect } from "vitest"
import {
  createPasteTracker,
  processInputChunk,
  resetPasteTracker,
  shouldTriggerClipboardPaste,
} from "../src/repl/inputChunkProcessor.js"

describe("inputChunkProcessor", () => {
  it("detects ctrl/meta clipboard shortcuts", () => {
    expect(shouldTriggerClipboardPaste({ ctrl: true, meta: false }, "v")).toBe(true)
    expect(shouldTriggerClipboardPaste({ ctrl: false, meta: true }, "v")).toBe(true)
    expect(shouldTriggerClipboardPaste({ ctrl: false, meta: false }, "v")).toBe(false)
  })

  it("merges fragmented bracketed paste sequences without stray characters", () => {
    const tracker = createPasteTracker()
    let value = ""
    const callbacks = {
      insertText: (text: string) => {
        value += text
      },
      submit: () => {
        value += "<enter>"
      },
      deleteWordBackward: () => {
        value = value.slice(0, -1)
      },
      deleteBackward: () => {
        value = value.slice(0, -1)
      },
      handlePastePayload: (payload: string) => {
        value += payload
      },
    }

    processInputChunk("pref", tracker, { ctrl: false, meta: false }, callbacks)

    const parts = ["\u001b", "[200", "~", "payload", "\u001b", "[201", "~"]
    for (const part of parts) {
      processInputChunk(part, tracker, { ctrl: false, meta: false }, callbacks)
    }

    expect(value).toBe("prefpayload")
    resetPasteTracker(tracker)
  })

  it("handles sequential bracketed pastes", () => {
    const tracker = createPasteTracker()
    let value = ""
    const callbacks = {
      insertText: (text: string) => {
        value += text
      },
      submit: () => {},
      deleteWordBackward: () => {},
      deleteBackward: () => {},
      handlePastePayload: (payload: string) => {
        value += `[${payload}]`
      },
    }

    processInputChunk("start", tracker, { ctrl: false, meta: false }, callbacks)
    processInputChunk("\u001b[200~first\u001b[201~", tracker, { ctrl: false, meta: false }, callbacks)
    processInputChunk("-mid-", tracker, { ctrl: false, meta: false }, callbacks)
    processInputChunk("\u001b[200~second\u001b[201~", tracker, { ctrl: false, meta: false }, callbacks)

    expect(value).toBe("start[first]-mid-[second]")
  })
})
