import React, { useState } from "react"
import stripAnsi from "strip-ansi"
import { describe, it, expect, beforeEach } from "vitest"
import { render } from "ink-testing-library"
import { LineEditor } from "../src/repl/components/LineEditor.js"

const delay = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms))

const createHarness = () => {
  const recorder = {
    value: "",
    cursor: 0,
  }

  const Harness: React.FC = () => {
    const [value, setValue] = useState("")
    const [cursor, setCursor] = useState(0)
    return (
      <LineEditor
        value={value}
        cursor={cursor}
        focus
        placeholder=""
        onChange={(next, nextCursor) => {
          recorder.value = next
          recorder.cursor = nextCursor
          setValue(next)
          setCursor(nextCursor)
        }}
        onSubmit={() => {}}
      />
    )
  }

  const result = render(<Harness />)
  const patchedStdin = result.stdin as StdinPatched
  patchedStdin.ref = () => patchedStdin
  patchedStdin.unref = () => patchedStdin

  return {
    ...result,
    stdin: patchedStdin,
    getValue: () => recorder.value,
  }
}

interface StdinPatched {
  ref: () => StdinPatched
  unref: () => StdinPatched
  write: (data: string) => void
}

describe("LineEditor clipboard + bracketed paste", () => {
  beforeEach(() => {
    delete process.env.BREADBOARD_FAKE_CLIPBOARD
  })

  it("inserts fake clipboard text when Ctrl+V fires", async () => {
    process.env.BREADBOARD_FAKE_CLIPBOARD = "pasted-words"
    const { stdin, getValue, unmount } = createHarness()
    await delay(5)
    stdin.write("prefix ")
    await delay(10)
    stdin.write("\u0016")
    await delay(20)
    expect(stripAnsi(getValue())).toBe("prefix pasted-words")
    unmount()
  })

  it("handles partial bracketed paste sequences without stray characters", async () => {
    const { stdin, getValue, unmount } = createHarness()
    await delay(5)
    stdin.write("pref")
    await delay(5)
    const chunks = ["\u001b", "[200", "~", "payload", "\u001b", "[201", "~"]
    for (const chunk of chunks) {
      stdin.write(chunk)
      await delay(5)
    }
    const output = stripAnsi(getValue())
    expect(output).toBe("prefpayload")
    expect(output.includes("~")).toBe(false)
    unmount()
  })
})
