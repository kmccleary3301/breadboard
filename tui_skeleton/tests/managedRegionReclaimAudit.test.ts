import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { buildManagedRegionReclaimAudit } from "../tools/reports/managedRegionReclaimAudit.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-reclaim-audit-"))
  tempDirs.push(dir)
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("managedRegionReclaimAudit", () => {
  it("summarizes pending retirement and settled truncation", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "viewport_resets.ndjson"),
      [JSON.stringify({ ts: 1 }), JSON.stringify({ ts: 2 })].join("\n") + "\n",
      "utf8",
    )
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ ts: 10, pendingResponse: true, landingShouldRetireNow: false, landingRetired: false, appendLandingToFeed: false }),
        JSON.stringify({ ts: 11, pendingResponse: true, landingShouldRetireNow: true, landingRetired: false, appendLandingToFeed: false }),
        JSON.stringify({ ts: 12, pendingResponse: false, landingRetired: true, warmLandingVisible: false, activeWindowHiddenCount: 3, activeWindowTruncated: true, transcriptCommittedCount: 4, transcriptTailCount: 0 }),
      ].join("\n") + "\n",
      "utf8",
    )

    const report = await buildManagedRegionReclaimAudit(dir)
    expect(report.viewportResetCount).toBe(2)
    expect(report.nonInitialViewportResetCount).toBe(1)
    expect(report.landingRetiredDuringPending).toBe(true)
    expect(report.firstPendingRetirementTs).toBe(11)
    expect(report.settledTruncationObserved).toBe(true)
    expect(report.maxHiddenCountAfterSettlement).toBe(3)
    expect(report.finalState.activeWindowTruncated).toBe(true)
  })
})
