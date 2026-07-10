import { describe, expect, it, vi } from "vitest"

import { pushHint } from "../src/commands/repl/controllerStateMethods.ts"
import { filterReadableHints, isLifecycleNoiseHint } from "../src/commands/repl/hintPolicy.ts"

describe("pushHint", () => {
  it("deduplicates consecutive identical visible hints", () => {
    const context = {
      hints: ["Session not ready yet."],
      pendingResponse: false,
      conversation: [{ id: "1" }],
      toolEvents: [],
      stats: { lastTurn: 1 },
      emitChange: vi.fn(),
    }

    pushHint.call(context, "Session not ready yet.")

    expect(context.hints).toEqual(["Session not ready yet."])
    expect(context.emitChange).not.toHaveBeenCalled()
  })

  it("still records non-consecutive repeated hints", () => {
    const context = {
      hints: ["Session not ready yet.", "Input is empty."],
      pendingResponse: false,
      conversation: [{ id: "1" }],
      toolEvents: [],
      stats: { lastTurn: 1 },
      emitChange: vi.fn(),
    }

    pushHint.call(context, "Session not ready yet.")

    expect(context.hints).toEqual(["Session not ready yet.", "Input is empty.", "Session not ready yet."])
    expect(context.emitChange).toHaveBeenCalledTimes(1)
  })

  it("drops lifecycle-only hints before they enter readable hint state", () => {
    const context = {
      hints: ["Input is empty."],
      pendingResponse: false,
      conversation: [{ id: "1" }],
      toolEvents: [],
      stats: { lastTurn: 1 },
      emitChange: vi.fn(),
    }

    pushHint.call(context, "Run finished (finish_reason:stop).")
    pushHint.call(context, "Log link available.")
    pushHint.call(context, "✻ Cooked for 2s")

    expect(context.hints).toEqual(["Input is empty."])
    expect(context.emitChange).not.toHaveBeenCalled()
  })

  it("classifies lifecycle hints consistently for defensive render filtering", () => {
    expect(isLifecycleNoiseHint("Log link available.")).toBe(true)
    expect(isLifecycleNoiseHint("Run finished (finish_reason:stop).")).toBe(true)
    expect(isLifecycleNoiseHint("✻ Cooked for 4s")).toBe(true)
    expect(isLifecycleNoiseHint("[warning] Stream gap detected")).toBe(false)
    expect(filterReadableHints(["Run finished.", "[warning] Stream gap detected"])).toEqual(["[warning] Stream gap detected"])
  })
})
