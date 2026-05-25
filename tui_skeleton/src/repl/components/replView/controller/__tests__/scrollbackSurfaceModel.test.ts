import { describe, expect, it } from "vitest"
import type { TranscriptItem } from "../../../../transcriptModel.js"
import { computeScrollbackSurfaceModel } from "../scrollbackSurfaceModel.js"

const msg = (id: string, createdAt: number, lines = 1): TranscriptItem => ({
  id,
  kind: "message",
  speaker: "assistant",
  text: `message ${id}`,
  phase: "final",
  createdAt,
  source: "conversation",
  lineCost: lines,
} as TranscriptItem & { lineCost: number })

const userMsg = (id: string, createdAt: number, lines = 1): TranscriptItem => ({
  id,
  kind: "message",
  speaker: "user",
  text: `message ${id}`,
  phase: "final",
  createdAt,
  source: "conversation",
  lineCost: lines,
} as TranscriptItem & { lineCost: number })

const measure = (entry: TranscriptItem) => entry.kind === "message" && entry.activePreviewLines
  ? entry.activePreviewLines
  : (entry as TranscriptItem & { lineCost?: number }).lineCost ?? 1

describe("computeScrollbackSurfaceModel", () => {
  it("keeps landing warm while the full transcript still fits in the managed region", () => {
    const committed = [msg("m1", 1, 2), msg("m2", 2, 2)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 10,
      landingRows: 4,
      landingAlways: false,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(true)
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["m1", "m2"])
    expect(model.warmCommitted).toEqual([])
    expect(model.activeWindow.items).toEqual([])
  })

  it("coldifies the oldest committed entries once the transcript exceeds the managed region", () => {
    const committed = [msg("m1", 1, 2), msg("m2", 2, 2), msg("m3", 3, 2), msg("m4", 4, 2)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 6,
      landingRows: 4,
      landingAlways: false,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(false)
    expect(model.warmCommitted).toEqual([])
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["m1", "m2", "m3", "m4"])
    expect(model.activeWindow.items).toEqual([])
  })

  it("keeps streaming tail hot even when all committed entries have gone cold", () => {
    const committed = [msg("m1", 1, 2), msg("m2", 2, 2), msg("m3", 3, 2)]
    const tail = [{ ...msg("tail", 4, 4), phase: "streaming", streaming: true } as TranscriptItem]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail,
      bodyBudgetRows: 4,
      landingRows: 4,
      landingAlways: false,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(false)
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["m1", "m2", "m3"])
    expect(model.warmCommitted.map((entry) => entry.id)).toEqual([])
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["tail"])
  })

  it("honors landingAlways by bypassing the warm landing path", () => {
    const committed = [msg("m1", 1, 1)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 10,
      landingRows: 4,
      landingAlways: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(false)
  })

  it("does not revive warm landing after it has been retired", () => {
    const committed = [msg("m1", 1, 1)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 10,
      landingRows: 4,
      landingAlways: false,
      landingRetired: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(false)
    expect(model.activeWindow.items).toEqual([])
  })

  it("keeps a settled user turn grouped with its reply instead of splitting prompt and answer", () => {
    const committed = [
      userMsg("u1", 1, 1),
      msg("a1", 2, 3),
      userMsg("u2", 3, 1),
      msg("a2", 4, 3),
    ]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 4,
      landingRows: 0,
      landingAlways: false,
      measureTranscriptEntryLines: measure,
    })
    expect(model.activeWindow.items).toEqual([])
    expect(model.activeWindow.hiddenCount).toBe(0)
    expect(model.activeWindow.truncated).toBe(false)
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["u1", "a1", "u2", "a2"])
  })

  it("keeps the latest committed turn warm while a response is still pending before the first tail delta arrives", () => {
    const committed = [userMsg("u1", 1, 1), msg("a1", 2, 2), userMsg("u2", 3, 1)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 6,
      landingRows: 0,
      landingAlways: false,
      pendingResponse: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["u2"])
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["u1", "a1"])
  })

  it("keeps only the latest committed prompt warm once the live tail has started", () => {
    const committed = [userMsg("u1", 1, 1), msg("a1", 2, 2), userMsg("u2", 3, 1)]
    const tail = [{ ...msg("a2", 4, 3), phase: "streaming", streaming: true } as TranscriptItem]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail,
      bodyBudgetRows: 8,
      landingRows: 0,
      landingAlways: false,
      pendingResponse: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["u2", "a2"])
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["u1", "a1"])
  })

  it("keeps landing warm during an active streamed turn when the active window still fits beside it", () => {
    const committed = [userMsg("u1", 1, 1), msg("a1", 2, 2), userMsg("u2", 3, 1)]
    const tail = [{ ...msg("a2", 4, 3), phase: "streaming", streaming: true } as TranscriptItem]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail,
      bodyBudgetRows: 8,
      landingRows: 4,
      landingAlways: false,
      pendingResponse: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(true)
    expect(model.transcriptBudgetRows).toBe(4)
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["u2", "a2"])
  })

  it("retires landing when the active window overshoots the reserved transcript budget", () => {
    const committed = [userMsg("u1", 1, 4)]
    const tail = [{ ...msg("a1", 2, 16), phase: "streaming", streaming: true } as TranscriptItem]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail,
      bodyBudgetRows: 27,
      landingRows: 13,
      landingAlways: false,
      pendingResponse: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(false)
    expect(model.transcriptBudgetRows).toBe(27)
    expect(model.activeWindow.usedLines).toBeGreaterThan(14)
  })

  it("retires landing during an active turn when reserving landing rows would leave no room for the active window", () => {
    const committed = [userMsg("u1", 1, 1)]
    const tail = [{ ...msg("a1", 2, 4), phase: "streaming", streaming: true } as TranscriptItem]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail,
      bodyBudgetRows: 4,
      landingRows: 4,
      landingAlways: false,
      pendingResponse: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.warmLandingVisible).toBe(false)
    expect(model.transcriptBudgetRows).toBe(4)
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["a1"])
  })

  it("keeps as many recent settled turn groups warm as fit when post-settlement preservation is enabled", () => {
    const committed = [userMsg("u1", 1, 1), msg("a1", 2, 2), userMsg("u2", 3, 1), msg("a2", 4, 2)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 8,
      landingRows: 0,
      landingAlways: false,
      preserveLatestSettledTurn: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["u1", "a1", "u2", "a2"])
    expect(model.warmCommitted.map((entry) => entry.id)).toEqual(["u1", "a1", "u2", "a2"])
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual([])
  })

  it("tail-slices the latest settled turn when preservation is enabled under height pressure", () => {
    const committed = [userMsg("u1", 1, 1), msg("a1", 2, 2), userMsg("u2", 3, 1), msg("a2", 4, 10)]
    const model = computeScrollbackSurfaceModel({
      committed,
      tail: [],
      bodyBudgetRows: 4,
      landingRows: 0,
      landingAlways: false,
      preserveLatestSettledTurn: true,
      measureTranscriptEntryLines: measure,
    })
    expect(model.activeWindow.items.map((entry) => entry.id)).toEqual(["a2"])
    expect(model.activeWindow.usedLines).toBeLessThanOrEqual(4)
    expect((model.activeWindow.items[0] as any).activePreviewLines).toBe(4)
    expect(model.activeWindow.truncated).toBe(true)
    expect(model.coldCommitted.map((entry) => entry.id)).toEqual(["u1", "a1", "u2"])
  })
})
