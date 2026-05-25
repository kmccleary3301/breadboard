import { describe, expect, it } from "vitest"
import type { TranscriptItem } from "../../../../transcriptModel.js"
import type { ScrollbackSurfaceModel } from "../scrollbackSurfaceModel.js"
import { applyMainBufferFollowSnapshot, captureMainBufferFollowSnapshot } from "../mainBufferFollow.js"

const msg = (id: string, speaker: "user" | "assistant", lines = 1): TranscriptItem => ({
  id,
  kind: "message",
  speaker,
  text: id,
  phase: "final",
  createdAt: 1,
  source: "conversation",
  lineCost: lines,
} as TranscriptItem & { lineCost: number })

const measure = (entry: TranscriptItem) => (entry as TranscriptItem & { lineCost?: number }).lineCost ?? 1

describe("mainBufferFollow", () => {
  it("captures the visible landing and active window for later freeze", () => {
    const model: ScrollbackSurfaceModel = {
      warmLandingVisible: true,
      coldCommitted: [msg("a1", "assistant")],
      warmCommitted: [msg("u2", "user")],
      activeWindow: {
        items: [msg("u2", "user"), msg("a2", "assistant", 3)],
        hiddenCount: 1,
        usedLines: 4,
        truncated: true,
      },
      transcriptBudgetRows: 6,
    }
    expect(captureMainBufferFollowSnapshot(model)).toEqual({
      warmLandingVisible: true,
      activeWindowItems: model.activeWindow.items,
    })
  })

  it("freezes the current visible turn while later tail items continue offscreen", () => {
    const u2 = msg("u2", "user")
    const a2 = msg("a2", "assistant", 3)
    const later = msg("a2b", "assistant", 2)
    const baseModel: ScrollbackSurfaceModel = {
      warmLandingVisible: false,
      coldCommitted: [],
      warmCommitted: [u2],
      activeWindow: {
        items: [u2, a2, later],
        hiddenCount: 2,
        usedLines: 7,
        truncated: true,
      },
      transcriptBudgetRows: 6,
    }
    const frozen = applyMainBufferFollowSnapshot(
      baseModel,
      { warmLandingVisible: true, activeWindowItems: [u2, a2] },
      [msg("u1", "user"), msg("a1", "assistant"), u2],
      [a2, later],
      measure,
    )

    expect(frozen.warmLandingVisible).toBe(true)
    expect(frozen.activeWindow.items.map((entry) => entry.id)).toEqual(["u2", "a2"])
    expect(frozen.coldCommitted.map((entry) => entry.id)).toEqual(["u1", "a1"])
    expect(frozen.activeWindow.hiddenCount).toBe(3)
    expect(frozen.activeWindow.truncated).toBe(true)
  })
})
