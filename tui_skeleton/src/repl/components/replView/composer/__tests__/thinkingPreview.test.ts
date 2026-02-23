import { describe, expect, it } from "vitest"
import { buildThinkingPreviewModel, getThinkingPreviewRowCount } from "../thinkingPreview.js"

describe("buildThinkingPreviewModel", () => {
  it("labels open lifecycle as starting", () => {
    const model = buildThinkingPreviewModel(
      {
        id: "tp-1",
        mode: "summary",
        lifecycle: "open",
        startedAt: 0,
        updatedAt: 0,
        closedAt: null,
        eventCount: 2,
        lines: ["line one", "line two"],
        truncated: false,
      },
      null,
      { showWhenClosed: true, maxLines: 5, cols: 120 },
    )
    expect(model?.phase).toBe("starting")
    expect(model?.headerLine).toContain("[task tree] starting")
  })

  it("labels updating lifecycle as responding", () => {
    const model = buildThinkingPreviewModel(
      {
        id: "tp-2",
        mode: "summary",
        lifecycle: "updating",
        startedAt: 0,
        updatedAt: 0,
        closedAt: null,
        eventCount: 3,
        lines: ["line one"],
        truncated: false,
      },
      null,
      { showWhenClosed: true, maxLines: 5, cols: 120 },
    )
    expect(model?.phase).toBe("responding")
    expect(model?.headerLine).toContain("[task tree] responding")
  })

  it("hides closed preview unless explicitly requested", () => {
    const hidden = buildThinkingPreviewModel(
      {
        id: "tp-3",
        mode: "summary",
        lifecycle: "closed",
        startedAt: 0,
        updatedAt: 0,
        closedAt: 1,
        eventCount: 2,
        lines: ["line one"],
        truncated: false,
      },
      null,
      { showWhenClosed: false, maxLines: 5, cols: 120 },
    )
    const shown = buildThinkingPreviewModel(
      {
        id: "tp-4",
        mode: "summary",
        lifecycle: "closed",
        startedAt: 0,
        updatedAt: 0,
        closedAt: 1,
        eventCount: 2,
        lines: ["line one"],
        truncated: false,
      },
      null,
      { showWhenClosed: true, maxLines: 5, cols: 120 },
    )
    expect(hidden).toBeNull()
    expect(shown?.phase).toBe("done")
  })

  it("maps artifact states to responding/done and preserves row count", () => {
    const active = buildThinkingPreviewModel(
      null,
      {
        id: "art-1",
        mode: "summary",
        startedAt: 0,
        updatedAt: 0,
        summary: "a\nb\nc",
      },
      { showWhenClosed: true, maxLines: 2, cols: 80 },
    )
    const done = buildThinkingPreviewModel(
      null,
      {
        id: "art-2",
        mode: "summary",
        startedAt: 0,
        updatedAt: 0,
        finalizedAt: 1,
        summary: "a\nb\nc",
      },
      { showWhenClosed: true, maxLines: 2, cols: 80 },
    )
    expect(active?.phase).toBe("responding")
    expect(done?.phase).toBe("done")
    expect(getThinkingPreviewRowCount(active)).toBe(3)
  })
})

