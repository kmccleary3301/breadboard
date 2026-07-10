import { describe, expect, it } from "vitest"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { evaluateMaintenanceWrapperToolSmoke } from "../tools/assertions/maintenanceWrapperToolSmokeCheck.ts"

const runCheck = async (snapshots: string, replState: string): Promise<unknown[]> => {
  const caseDir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-maint-tool-check-"))
  await fs.writeFile(path.join(caseDir, "pty_snapshots.txt"), snapshots, "utf8")
  await fs.writeFile(path.join(caseDir, "repl_state.ndjson"), replState, "utf8")
  try {
    return await evaluateMaintenanceWrapperToolSmoke(caseDir)
  } finally {
    await fs.rm(caseDir, { recursive: true, force: true })
  }
}

describe("maintenanceWrapperToolSmokeCheck", () => {
  it("accepts the local mock tool projection contract", async () => {
    const anomalies = await runCheck(
      `# tool-first-result\n● list_dir\n\n# tool-settled-history\n● list_dir\n● apply_unified_patch\nImplementation receipts and verification receipts are present, so I am closing the task without running more tools.\nVerification: verification receipt present\n`,
      JSON.stringify({ state: { lastToolEvent: { kind: "call", status: "success", text: "apply_unified_patch" } } }) + "\n",
    )
    expect(anomalies).toEqual([])
  })

  it("reports missing tool rows", async () => {
    const anomalies = await runCheck(
      `# tool-first-result\n\n# tool-settled-history\nImplementation receipts and verification receipts are present, so I am closing the task without running more tools.\nVerification: verification receipt present\n`,
      JSON.stringify({ state: { lastToolEvent: { kind: "status", status: "success", text: "status" } } }) + "\n",
    )
    expect(anomalies).not.toEqual([])
  })
})
