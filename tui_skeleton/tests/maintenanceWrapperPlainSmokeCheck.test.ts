import { describe, expect, it } from "vitest"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { evaluateMaintenanceWrapperPlainSmoke } from "../tools/assertions/maintenanceWrapperPlainSmokeCheck.ts"

const runCheck = async (snapshots: string, surfaceModel: string): Promise<unknown[]> => {
  const caseDir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-maint-plain-check-"))
  await fs.writeFile(path.join(caseDir, "pty_snapshots.txt"), snapshots, "utf8")
  await fs.writeFile(path.join(caseDir, "surface_model.ndjson"), surfaceModel, "utf8")
  try {
    return await evaluateMaintenanceWrapperPlainSmoke(caseDir)
  } finally {
    await fs.rm(caseDir, { recursive: true, force: true })
  }
}

describe("maintenanceWrapperPlainSmokeCheck", () => {
  it("accepts the local mock settled contract", async () => {
    const anomalies = await runCheck(
      `# after-submit\nTips for getting started\n\n# after-answer\n❯ Answer with exactly: PING_OK\nImplementation receipts and verification receipts are present, so I am closing the task without running more tools.\nFiles changed: Makefile, protofilesystem.h, protofilesystem.c, test_filesystem.c\nVerification: verification receipt present\n• [ready] last 0s · enter send\n`,
      [
        JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: false, appendLandingToFeed: false, warmLandingVisible: true }),
        JSON.stringify({ pendingResponse: false, transcriptCommittedCount: 2, transcriptTailCount: 0, landingRetired: true, appendLandingToFeed: true, warmLandingVisible: false }),
      ].join("\n") + "\n",
    )
    expect(anomalies).toEqual([])
  })

  it("reports missing settled mock C-filesystem receipt output", async () => {
    const anomalies = await runCheck(
      `# after-submit\nTips for getting started\n\n# after-answer\n❯ Answer with exactly: PING_OK\n• [ready] last 0s · enter send\n`,
      JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: false, appendLandingToFeed: false, warmLandingVisible: true }) + "\n",
    )
    expect(anomalies).not.toEqual([])
  })
})
