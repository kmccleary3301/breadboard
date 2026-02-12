import { describe, it, expect, vi } from "vitest"
import { render } from "ink-testing-library"
import React from "react"
import { LineEditor } from "../LineEditor.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe("LineEditor", () => {
  it("handles backspace", async () => {
    const handleChange = vi.fn()
    const { stdin } = render(
      <LineEditor value="abc" cursor={3} focus placeholder="test" onChange={handleChange} onSubmit={() => {}} />,
    )
    await flush()
    stdin.write("\x7f")
    await flush()
    expect(handleChange).toHaveBeenCalledWith("ab", 2)
  })
})
