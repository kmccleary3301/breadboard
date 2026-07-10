import { describe, expect, it } from "vitest"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { evaluateMaintenanceWrapperMultiturnOrdering } from "../tools/assertions/maintenanceWrapperMultiturnOrderingCheck.ts"

const runCheck = async (snapshots: string): Promise<unknown[]> => {
  const caseDir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-maint-multiturn-check-"))
  await fs.writeFile(path.join(caseDir, "pty_snapshots.txt"), snapshots, "utf8")
  try {
    return await evaluateMaintenanceWrapperMultiturnOrdering(caseDir)
  } finally {
    await fs.rm(caseDir, { recursive: true, force: true })
  }
}

describe("maintenanceWrapperMultiturnOrderingCheck", () => {
  it("accepts ordered prompt history without relying on literal answer words", async () => {
    const anomalies = await runCheck(`
# turn1-history
❯ What is 1+1? Answer with only the lowercase English word.
Proceed to build and test.

# turn2-history
❯ What is 1+1? Answer with only the lowercase English word.
Proceed to build and test.
❯ What is 2+2? Answer with only the lowercase English word.
Proceed to build and test.

# turn3-history
❯ What is 2+2? Answer with only the lowercase English word.
Proceed to build and test.
❯ What is 3+3? Answer with only the lowercase English word.
Proceed to build and test.
`)
    expect(anomalies).toEqual([])
  })

  it("reports prompt reordering or duplication", async () => {
    const anomalies = await runCheck(`
# turn1-history
❯ What is 1+1? Answer with only the lowercase English word.

# turn2-history
❯ What is 2+2? Answer with only the lowercase English word.
❯ What is 1+1? Answer with only the lowercase English word.

# turn3-history
❯ What is 2+2? Answer with only the lowercase English word.
❯ What is 2+2? Answer with only the lowercase English word.
❯ What is 3+3? Answer with only the lowercase English word.
`)
    expect(anomalies).not.toEqual([])
  })
})
