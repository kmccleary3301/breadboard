import { describe, expect, it } from "vitest"
import { getShikiStatus, maybeHighlightCode, subscribeShiki } from "../src/repl/shikiHighlighter.js"

const highlightWhenReady = (code: string, lang: string): Promise<string[]> =>
  new Promise((resolve, reject) => {
    let unsubscribe = () => {}

    const attempt = () => {
      const lines = maybeHighlightCode(code, lang)
      if (lines) {
        unsubscribe()
        resolve(lines)
        return
      }

      const state = getShikiStatus()
      if (state.status === "failed") {
        unsubscribe()
        reject(new Error(state.error ?? "Shiki failed to load"))
      }
    }

    unsubscribe = subscribeShiki(attempt)
    attempt()
  })

describe("Shiki language loading", () => {
  it("lazily loads a valid bundled language alias", async () => {
    const lines = await highlightWhenReady("puts 'hello'", "rb")

    expect(lines).toHaveLength(1)
    expect(lines[0]).toContain("puts")
  })
})
