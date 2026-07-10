import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateDebugStreamCorrelation } from "../tools/assertions/debugStreamCorrelationCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-debug-stream-corr-"))
  tempDirs.push(dir)
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("debugStreamCorrelationCheck", () => {
  it("passes when all three debug streams carry matching qcBatchId and qcCaseId", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "config.json"), JSON.stringify({ env: { BREADBOARD_QC_BATCH_ID: "batch-1", BREADBOARD_QC_CASE_ID: "case-a" } }) + "\n", "utf8")
    const viewportRow = JSON.stringify({ ts: 1, iso: "2026-04-15T00:00:00.000Z", qcBatchId: "batch-1", qcCaseId: "case-a", event: "managed_viewport_reset_key", sessionId: "session-1", resetKey: "abc" }) + "\n"
    const surfaceRow = JSON.stringify({ ts: 1, iso: "2026-04-15T00:00:00.000Z", qcBatchId: "batch-1", qcCaseId: "case-a", event: "surface_model", sessionId: "session-1", landingVariant: "board", pendingResponse: false, transcriptCommittedCount: 0, transcriptTailCount: 0 }) + "\n"
    const feedRow = JSON.stringify({ ts: 1, iso: "2026-04-15T00:00:00.000Z", qcBatchId: "batch-1", qcCaseId: "case-a", event: "feed_reset", sessionId: "session-1", enabled: true }) + "\n"
    await Promise.all([
      writeFile(path.join(dir, "viewport_resets.ndjson"), viewportRow, "utf8"),
      writeFile(path.join(dir, "surface_model.ndjson"), surfaceRow, "utf8"),
      writeFile(path.join(dir, "scrollback_feed.ndjson"), feedRow, "utf8"),
    ])
    await expect(evaluateDebugStreamCorrelation(dir)).resolves.toEqual([])
  })

  it("fails when a debug stream record is missing the expected ids", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "config.json"), JSON.stringify({ env: { BREADBOARD_QC_BATCH_ID: "batch-1", BREADBOARD_QC_CASE_ID: "case-a" } }) + "\n", "utf8")
    await writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 1, iso: "2026-04-15T00:00:00.000Z", qcBatchId: "batch-1", qcCaseId: "case-a", event: "managed_viewport_reset_key", sessionId: "session-1", resetKey: "abc" }) + "\n", "utf8")
    await writeFile(path.join(dir, "surface_model.ndjson"), JSON.stringify({ ts: 1, iso: "2026-04-15T00:00:00.000Z", qcBatchId: "batch-1", event: "surface_model", sessionId: "session-1", landingVariant: "board", pendingResponse: false, transcriptCommittedCount: 0, transcriptTailCount: 0 }) + "\n", "utf8")
    await writeFile(path.join(dir, "scrollback_feed.ndjson"), JSON.stringify({ ts: 1, iso: "2026-04-15T00:00:00.000Z", qcBatchId: "batch-1", qcCaseId: "case-a", event: "feed_reset", sessionId: "session-1", enabled: true }) + "\n", "utf8")
    await expect(evaluateDebugStreamCorrelation(dir)).resolves.toEqual([
      {
        id: "debug-stream-surface_model-case-mismatch-0",
        message: "surface_model record 0 missing qcCaseId=case-a",
      },
    ])
  })
})
