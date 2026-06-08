import { describe, expect, it } from "vitest"
import {
  normalizeTableJsonOutputMode,
  normalizeTextJsonOutputMode,
} from "../src/commands/commandOutput.js"

describe("commandOutput", () => {
  it("normalizes text/json output mode", () => {
    expect(normalizeTextJsonOutputMode("json")).toBe("json")
    expect(normalizeTextJsonOutputMode("text")).toBe("text")
    expect(normalizeTextJsonOutputMode("other")).toBe("text")
  })

  it("normalizes table/json output mode", () => {
    expect(normalizeTableJsonOutputMode("json")).toBe("json")
    expect(normalizeTableJsonOutputMode("table")).toBe("table")
    expect(normalizeTableJsonOutputMode("other")).toBe("table")
  })
})
