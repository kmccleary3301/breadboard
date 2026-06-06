import { describe, expect, it } from "vitest"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawn } from "node:child_process"

const ROOT_DIR = path.resolve(__dirname, "..")

const runCheck = async (snapshots: string): Promise<unknown[]> => {
  const caseDir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-maint-multiturn-check-"))
  await fs.writeFile(path.join(caseDir, "pty_snapshots.txt"), snapshots, "utf8")
  try {
    const stdout = await new Promise<string>((resolve, reject) => {
      const child = spawn("pnpm", ["exec", "tsx", "tools/assertions/maintenanceWrapperMultiturnOrderingCheck.ts", "--case-dir", caseDir], {
        cwd: ROOT_DIR,
        stdio: ["ignore", "pipe", "pipe"],
      })
      let out = ""
      let err = ""
      child.stdout.on("data", (chunk) => (out += String(chunk)))
      child.stderr.on("data", (chunk) => (err += String(chunk)))
      child.on("error", reject)
      child.on("close", (code) => {
        if (code !== 0) reject(new Error(err || out || `exit ${code}`))
        else resolve(out)
      })
    })
    return JSON.parse(stdout)
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
