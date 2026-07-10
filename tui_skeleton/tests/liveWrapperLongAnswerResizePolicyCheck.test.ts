import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLiveWrapperLongAnswerResizePolicy } from "../tools/assertions/liveWrapperLongAnswerResizePolicyCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-live-long-answer-policy-"))
  tempDirs.push(dir)
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("liveWrapperLongAnswerResizePolicyCheck", () => {
  it("passes when the canonical long-answer resize contract is clean", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ ts: 1, pendingResponse: true, landingRetired: false, landingShouldRetireNow: false, appendLandingToFeed: false, activeWindowHiddenCount: 0, activeWindowTruncated: false }),
        JSON.stringify({ ts: 2, pendingResponse: false, landingRetired: true, landingShouldRetireNow: false, appendLandingToFeed: true, activeWindowHiddenCount: 0, activeWindowTruncated: false, warmLandingVisible: false, transcriptCommittedCount: 3, transcriptTailCount: 0 }),
      ].join("\n") + "\n",
      "utf8",
    )
    await writeFile(
      path.join(dir, "viewport_resets.ndjson"),
      [
        JSON.stringify({ ts: 1, resetKey: "idle-start", pendingResponse: false }),
        JSON.stringify({ ts: 2, resetKey: "active", pendingResponse: true }),
        JSON.stringify({ ts: 3, resetKey: "idle-settled", pendingResponse: false }),
      ].join("\n") + "\n",
      "utf8",
    )
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# streaming-before-resize",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-",
        "❯",
        "[responding] elapsed 1s - follow live",
        "",
        "# streaming-after-resize-small",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-01",
        "- item-02",
        "❯",
        "[responding] elapsed 1s - follow live",
        "",
        "# settled-history",
        "- item-01",
        "- item-02",
        "- item-20",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "markdown_paint_summary.json"),
      JSON.stringify({
        staticFeedAppendCount: 3,
        staticChunkDuplicateCount: 0,
        frozenBlockMutationCount: 0,
        activeFrozenOverlapCount: 0,
        maxHotTailRows: 1,
        broadClearCount: 0,
        unknownAttributionRows: 0,
      }),
      "utf8",
    )

    await expect(evaluateLiveWrapperLongAnswerResizePolicy(dir)).resolves.toEqual([])
  })

  it("fails when the active prompt is interleaved below streamed assistant output", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ ts: 1, pendingResponse: true, landingRetired: false, landingShouldRetireNow: false, appendLandingToFeed: false, activeWindowHiddenCount: 0, activeWindowTruncated: false }),
        JSON.stringify({ ts: 2, pendingResponse: false, landingRetired: true, landingShouldRetireNow: false, appendLandingToFeed: true, activeWindowHiddenCount: 0, activeWindowTruncated: false, warmLandingVisible: false, transcriptCommittedCount: 3, transcriptTailCount: 0 }),
      ].join("\n") + "\n",
      "utf8",
    )
    await writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 1 }) + "\n", "utf8")
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# streaming-after-resize-small",
        "- item-01",
        "- item-02",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-03",
        "❯",
        "[responding] elapsed 1s - follow live",
        "",
        "# settled-history",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-01",
        "- item-20",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "markdown_paint_summary.json"),
      JSON.stringify({
        staticFeedAppendCount: 3,
        staticChunkDuplicateCount: 0,
        frozenBlockMutationCount: 0,
        activeFrozenOverlapCount: 0,
        maxHotTailRows: 1,
        broadClearCount: 0,
        unknownAttributionRows: 0,
      }),
      "utf8",
    )

    const result = await evaluateLiveWrapperLongAnswerResizePolicy(dir)
    expect(result.map((entry) => entry.id)).toEqual([
      "streaming-prompt-interleaved-with-output-streaming-after-resize-small",
    ])
  })

  it("fails when a streaming frame loses the active output", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ ts: 1, pendingResponse: true, landingRetired: false, landingShouldRetireNow: false, appendLandingToFeed: false, activeWindowHiddenCount: 0, activeWindowTruncated: false }),
        JSON.stringify({ ts: 2, pendingResponse: false, landingRetired: true, landingShouldRetireNow: false, appendLandingToFeed: true, activeWindowHiddenCount: 0, activeWindowTruncated: false, warmLandingVisible: false, transcriptCommittedCount: 3, transcriptTailCount: 0 }),
      ].join("\n") + "\n",
      "utf8",
    )
    await writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 1 }) + "\n", "utf8")
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# streaming-after-resize-large",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "[working] last 1s - enter send",
        "",
        "# settled-history",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-20",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "markdown_paint_summary.json"),
      JSON.stringify({
        staticFeedAppendCount: 3,
        staticChunkDuplicateCount: 0,
        frozenBlockMutationCount: 0,
        activeFrozenOverlapCount: 0,
        maxHotTailRows: 1,
        broadClearCount: 0,
        unknownAttributionRows: 0,
      }),
      "utf8",
    )

    const result = await evaluateLiveWrapperLongAnswerResizePolicy(dir)
    expect(result.map((entry) => entry.id)).toEqual([
      "streaming-frame-missing-active-output-streaming-after-resize-large",
    ])
  })

  it("fails when a streaming frame loses the bottom composer/status boundary", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ ts: 1, pendingResponse: true, landingRetired: false, landingShouldRetireNow: false, appendLandingToFeed: false, activeWindowHiddenCount: 0, activeWindowTruncated: false }),
        JSON.stringify({ ts: 2, pendingResponse: false, landingRetired: true, landingShouldRetireNow: false, appendLandingToFeed: true, activeWindowHiddenCount: 0, activeWindowTruncated: false, warmLandingVisible: false, transcriptCommittedCount: 3, transcriptTailCount: 0 }),
      ].join("\n") + "\n",
      "utf8",
    )
    await writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 1 }) + "\n", "utf8")
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# streaming-after-resize-small",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-01",
        "- item-02",
        "",
        "",
        "",
        "# settled-history",
        "❯ Answer with exactly 20 short bullet points and no preface.",
        "- item-20",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "markdown_paint_summary.json"),
      JSON.stringify({
        staticFeedAppendCount: 3,
        staticChunkDuplicateCount: 0,
        frozenBlockMutationCount: 0,
        activeFrozenOverlapCount: 0,
        maxHotTailRows: 1,
        broadClearCount: 0,
        unknownAttributionRows: 0,
      }),
      "utf8",
    )

    const result = await evaluateLiveWrapperLongAnswerResizePolicy(dir)
    expect(result.map((entry) => entry.id)).toEqual([
      "streaming-frame-missing-bottom-boundary-streaming-after-resize-small",
    ])
  })

  it("fails when long streamed markdown is not frozen safely into static scrollback", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      JSON.stringify({ ts: 1, pendingResponse: false, landingRetired: true, appendLandingToFeed: true, activeWindowHiddenCount: 0, activeWindowTruncated: false }) + "\n",
      "utf8",
    )
    await writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 1 }) + "\n", "utf8")
    await writeFile(path.join(dir, "pty_snapshots.txt"), "# settled-history\n- item-20\n", "utf8")
    await writeFile(
      path.join(dir, "markdown_paint_summary.json"),
      JSON.stringify({
        staticFeedAppendCount: 0,
        staticChunkDuplicateCount: 1,
        frozenBlockMutationCount: 2,
        activeFrozenOverlapCount: 3,
        maxHotTailRows: 4,
        broadClearCount: 5,
        unknownAttributionRows: 6,
      }),
      "utf8",
    )

    const result = await evaluateLiveWrapperLongAnswerResizePolicy(dir)
    expect(result.map((entry) => entry.id)).toEqual([
      "markdown-static-chunk-duplicate",
      "markdown-frozen-block-mutated",
      "markdown-active-frozen-overlap",
      "markdown-hot-tail-unbounded",
      "markdown-broad-clear",
      "markdown-unknown-attribution",
    ])
  })

  it("allows pending landing retirement when a committed static-feed snapshot preserves it", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({
          ts: 1,
          pendingResponse: true,
          landingRetired: true,
          landingShouldRetireNow: false,
          appendLandingToFeed: true,
          landingLifecycleCommittedSnapshot: true,
          activeWindowHiddenCount: 0,
          activeWindowTruncated: false,
        }),
        JSON.stringify({
          ts: 2,
          pendingResponse: false,
          landingRetired: true,
          appendLandingToFeed: true,
          landingLifecycleCommittedSnapshot: true,
          activeWindowHiddenCount: 0,
          activeWindowTruncated: false,
          warmLandingVisible: false,
          transcriptCommittedCount: 3,
          transcriptTailCount: 0,
        }),
      ].join("\n") + "\n",
      "utf8",
    )
    await writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 1 }) + "\n", "utf8")
    await writeFile(path.join(dir, "pty_snapshots.txt"), "# settled-history\nlanding\n- item-20\n", "utf8")

    await expect(evaluateLiveWrapperLongAnswerResizePolicy(dir)).resolves.toEqual([])
  })
})
