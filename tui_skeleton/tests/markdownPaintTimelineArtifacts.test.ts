import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { buildTimelineArtifacts } from "../tools/timeline/buildTimeline.ts"

describe("P12 markdown paint timeline artifacts", () => {
  it("writes baseline paint events and summary from grid deltas", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "bb-p12-paint-"))
    try {
      await writeFile(
        path.join(dir, "pty_metadata.json"),
        JSON.stringify({
          startedAt: 1000,
          rows: 10,
          cols: 80,
          resizeStats: {
            count: 1,
            minCols: 60,
            maxCols: 80,
            minRows: 10,
            maxRows: 10,
            burstMs: 10,
            firstEventOffsetMs: 20,
            lastEventOffsetMs: 20,
          },
        }),
        "utf8",
      )
      await writeFile(
        path.join(dir, "grid_deltas.ndjson"),
        [
          JSON.stringify({ timestamp: 1000, changedLines: [1, 2], bytes: 20 }),
          JSON.stringify({ timestamp: 1020, changedLines: [1, 2, 3], bytes: 30 }),
          JSON.stringify({ timestamp: 1040, changedLines: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], bytes: 100 }),
        ].join("\n") + "\n",
        "utf8",
      )
      await writeFile(path.join(dir, "sse_events.txt"), "data: {\"type\":\"turn_start\",\"timestamp\":2500}\n\n", "utf8")
      await writeFile(path.join(dir, "anomalies.json"), "[]\n", "utf8")
      await writeFile(
        path.join(dir, "markdown_metrics.ndjson"),
        [
          JSON.stringify({
            event: "markdown_document_update",
            messageId: "msg-1",
            blockCount: 2,
            changedBlockIds: ["b1", "b2"],
            finalizedBlockIds: ["b1"],
          }),
          JSON.stringify({
            event: "markdown_render_cache",
            result: "miss",
            messageId: "msg-1",
            chunkId: "msg-1/chunk/b1",
            blockIds: ["b1"],
            rowCount: 1,
          }),
          JSON.stringify({
            event: "markdown_render_cache",
            result: "hit",
            messageId: "msg-1",
            chunkId: "msg-1/chunk/b1",
            blockIds: ["b1"],
            rowCount: 1,
          }),
          JSON.stringify({
            event: "markdown_chunk_promote",
            messageId: "msg-1",
            chunkId: "msg-1/chunk/b1",
            blockIds: ["b1"],
            rowCount: 1,
          }),
          JSON.stringify({
            event: "markdown_hot_tail",
            messageId: "msg-1",
            blockIds: ["b2"],
            rowCount: 2,
          }),
        ].join("\n") + "\n",
        "utf8",
      )

      const result = await buildTimelineArtifacts(dir)
      const events = await readFile(result.markdownPaintPath, "utf8")
      const timelineSummary = JSON.parse(await readFile(result.summaryPath, "utf8"))
      const summary = JSON.parse(await readFile(result.markdownPaintSummaryPath, "utf8"))
      const ownership = JSON.parse(await readFile(result.blockOwnershipSummaryPath, "utf8"))
      const attribution = JSON.parse(await readFile(result.paintAttributionSummaryPath, "utf8"))

      expect(events).toContain('"event":"paint_budget"')
      expect(events).toContain('"cause":"resize"')
      expect(timelineSummary.ttftSeconds).toBe(1.5)
      expect(summary).toMatchObject({
        schemaVersion: 1,
        phase: "p12-instrumentation-baseline",
        correctnessAnomalies: 0,
        ptyEvents: 3,
        sseEvents: 1,
        resizeBurstFrames: 1,
        nonResizeFrames: 2,
        staticChunkDuplicateCount: 0,
        frozenBlockMutationCount: 0,
        maxHotTailRows: 2,
      })
      expect(summary.blockCount).toBe(2)
      expect(summary.changedBlockCount).toBe(2)
      expect(summary.finalizedBlockCount).toBe(1)
      expect(summary.cacheHitRate).toBe(0.5)
      expect(summary.cacheMissCount).toBe(1)
      expect(summary.staticFeedAppendCount).toBe(1)
      expect(summary.maxHotTailRows).toBe(2)
      expect(summary.broadClearCount).toBe(1)
      expect(summary.unknownAttributionRows).toBe(15)
      expect(summary.meanRowsChangedPctNonResize).toBeCloseTo(0.6)
      expect(ownership).toMatchObject({
        schemaVersion: 1,
        phase: "p12-instrumentation-baseline",
        semanticMessageCount: 1,
        blockCount: 2,
        hotBlockCount: 1,
        frozenStaticBlockCount: 1,
        staticChunkDuplicateCount: 0,
        frozenBlockMutationCount: 0,
      })
      expect(attribution).toMatchObject({
        schemaVersion: 1,
        phase: "p12-instrumentation-baseline",
        totalRowsChanged: 15,
        frozenChunkRowsChanged: 0,
        unknownRowsChanged: 15,
        unknownRowsChangedPct: 1,
      })
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
