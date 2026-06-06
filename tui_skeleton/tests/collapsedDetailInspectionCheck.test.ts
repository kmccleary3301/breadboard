import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateCollapsedDetailInspection } from "../tools/assertions/collapsedDetailInspectionCheck.ts"

const writeCase = async (snapshots: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-collapsed-detail-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  return dir
}

describe("evaluateCollapsedDetailInspection", () => {
  it("accepts collapsed-detail inspection evidence", async () => {
    const dir = await writeCase(
      [
        "# collapsed-summary",
        "12 lines hidden · use [ / ] to target, then press e expand / o inspect",
        "# detail-open",
        "Message detail",
        "Detail line 01",
        "# detail-close",
        "12 lines hidden · use [ / ] to target, then press e expand / o inspect",
        "",
      ].join("\n"),
    )
    await expect(evaluateCollapsedDetailInspection(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing detail overlay evidence", async () => {
    const dir = await writeCase(
      [
        "# collapsed-summary",
        "collapsed",
        "# detail-open",
        "overlay",
        "# detail-close",
        "overlay",
        "",
      ].join("\n"),
    )
    const anomalies = await evaluateCollapsedDetailInspection(dir)
    expect(anomalies.map((item) => item.id)).toContain("collapsed-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("detail-open-missing")
    expect(anomalies.map((item) => item.id)).toContain("detail-close-return-missing")
    await rm(dir, { recursive: true, force: true })
  })
})
