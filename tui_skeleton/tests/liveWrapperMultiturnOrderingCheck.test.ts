import { describe, expect, it } from "vitest"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawn } from "node:child_process"

const ROOT_DIR = path.resolve(__dirname, "..")

const runCheck = async (snapshots: string): Promise<unknown[]> => {
  const caseDir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-multiturn-check-"))
  await fs.writeFile(path.join(caseDir, "pty_snapshots.txt"), snapshots, "utf8")
  try {
    const stdout = await new Promise<string>((resolve, reject) => {
      const child = spawn("pnpm", ["exec", "tsx", "tools/assertions/liveWrapperMultiturnOrderingCheck.ts", "--case-dir", caseDir], {
        cwd: ROOT_DIR,
        stdio: ["ignore", "pipe", "pipe"],
      })
      let out = ""
      let err = ""
      child.stdout.on("data", (chunk) => (out += String(chunk)))
      child.stderr.on("data", (chunk) => (err += String(chunk)))
      child.on("error", reject)
      child.on("close", (code) => {
        if (code !== 0) {
          reject(new Error(err || out || `exit ${code}`))
        } else {
          resolve(out)
        }
      })
    })
    return JSON.parse(stdout)
  } finally {
    await fs.rm(caseDir, { recursive: true, force: true })
  }
}

describe("liveWrapperMultiturnOrderingCheck", () => {
  it("accepts correctly ordered multi-turn snapshots even when the oldest prompt scrolls out by turn 3", async () => {
    const anomalies = await runCheck(`
# turn1-history
two

# turn2-history
two
four

# turn3-history
two
four
six
`)
    expect(anomalies).toEqual([])
  })

  it("reports out-of-order transcript state", async () => {
    const anomalies = await runCheck(`
# turn1-history
two

# turn2-history
two
four

# turn3-history
two
four
four
six
`)
    expect(anomalies).not.toEqual([])
  })

  it("reports stale prompt concatenation", async () => {
    const anomalies = await runCheck(`
# turn1-history
❯ What is 1+1? Answer with only the lowercase English word.
two

# turn2-history
❯ What is 1+1? Answer with only the lowercase English word.What is 2+2? Answer with only the lowercase English word.
❯ What is 2+2? Answer with only the lowercase English word.
four

# turn3-history
two
four
six
`)
    expect(anomalies).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "concatenated-user-prompt" }),
      ]),
    )
  })

  it("reports floating lifecycle and log-link chrome in settled transcript snapshots", async () => {
    const anomalies = await runCheck(`
# turn1-history
two
● Log · file://logging/20260430-203935_ray_SCE

# turn2-history
two
four

# turn3-history
two
four
six
`)
    expect(anomalies).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "floating-lifecycle-hint" }),
      ]),
    )
  })
})
