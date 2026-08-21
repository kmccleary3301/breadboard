import { afterEach, describe, it, expect, vi } from "vitest"
import { render } from "ink-testing-library"
import React from "react"
import { LineEditor } from "../LineEditor.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe("LineEditor", () => {
  afterEach(() => {
    delete process.env.BREADBOARD_FAKE_CLIPBOARD
  })

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

  it("treats split CSI-u Shift+Enter as a newline instead of printable residue", async () => {
    const handleChange = vi.fn()
    const { stdin } = render(
      <LineEditor value="first line" cursor={10} focus placeholder="test" onChange={handleChange} onSubmit={() => {}} />,
    )
    await flush()
    stdin.write("\x1b")
    await flush()
    stdin.write("[13;2u")
    await flush()
    expect(handleChange).toHaveBeenLastCalledWith("first line\n", 11)
  })

  it("treats bare CSI-u Shift+Enter residue from PTY hosts as a newline", async () => {
    const handleChange = vi.fn()
    const { stdin } = render(
      <LineEditor value="first line" cursor={10} focus placeholder="test" onChange={handleChange} onSubmit={() => {}} />,
    )
    await flush()
    stdin.write("[13;2u")
    await flush()
    expect(handleChange).toHaveBeenLastCalledWith("first line\n", 11)
  })

  it("keeps slowly typed literal CSI-u text as normal input", async () => {
    const handleChange = vi.fn()
    const { stdin } = render(
      <LineEditor value="first line" cursor={10} focus placeholder="test" onChange={handleChange} onSubmit={() => {}} />,
    )
    await flush()
    for (const char of "[13;2u") {
      stdin.write(char)
      await flush()
    }
    await flush()
    expect(handleChange).toHaveBeenLastCalledWith("first line[13;2u", 16)
  })

  it("routes fake clipboard images to attachment handling on Ctrl+V", async () => {
    process.env.BREADBOARD_FAKE_CLIPBOARD =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9YpDkdIAAAAASUVORK5CYII="
    const handleAttachment = vi.fn()
    const { stdin } = render(
      <LineEditor
        value=""
        cursor={0}
        focus
        placeholder="test"
        onChange={() => {}}
        onSubmit={() => {}}
        onPasteAttachment={handleAttachment}
      />,
    )
    await flush()
    stdin.write("\x16")
    await vi.waitFor(() =>
      expect(handleAttachment).toHaveBeenCalledWith(expect.objectContaining({ kind: "image", mime: "image/png", size: 68 })),
    )
  })
})
