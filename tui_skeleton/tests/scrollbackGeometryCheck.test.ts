import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import { evaluateScrollbackGeometry } from "../tools/assertions/scrollbackGeometryCheck.ts"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })))
})

const makeCase = async (records: readonly unknown[] | null) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-scrollback-geometry-"))
  tempDirs.push(dir)
  if (records) {
    await fs.writeFile(
      path.join(dir, "render_timeline.ndjson"),
      records.map((record) => JSON.stringify(record)).join("\n"),
      "utf8",
    )
  }
  return dir
}

describe("scrollbackGeometryCheck", () => {
  it("accepts actual row-source shell geometry", async () => {
    const dir = await makeCase([{ event: "shell_geometry", rowSource: "actual", resolvedRows: 24, stdoutRows: 24, processRows: 24 }])
    await expect(evaluateScrollbackGeometry(dir)).resolves.toEqual([])
  })

  it("flags missing row-source diagnostics", async () => {
    const dir = await makeCase(null)
    const anomalies = await evaluateScrollbackGeometry(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("shell-geometry-missing")
  })

  it("flags virtual-height rows even when a row source is present", async () => {
    const dir = await makeCase([{ event: "shell_geometry", rowSource: "fallback", resolvedRows: 100_000, stdoutRows: 100_000 }])
    const anomalies = await evaluateScrollbackGeometry(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("shell-geometry-row-source-not-actual")
    expect(anomalies.map((entry) => entry.id)).toContain("shell-geometry-virtual-height")
  })
})
