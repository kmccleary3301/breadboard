import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateComposerContinuationCues } from "../tools/assertions/composerContinuationCuesCheck.ts"

const writeCase = async (snapshots: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-composer-continuation-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  return dir
}

describe("evaluateComposerContinuationCues", () => {
  it("accepts bounded continuation cues near the composer", async () => {
    const dir = await writeCase([
      "# idle-continuation",
      "resume /sessions",
      "@ attach",
      "ctrl+o transcript",
      "ctrl+k model",
      "",
    ].join("\n"))
    await expect(evaluateComposerContinuationCues(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing or stale continuation cues", async () => {
    const dir = await writeCase([
      "# idle-continuation",
      "/ commands",
      "@ files",
      "ctrl+o transcript",
      "",
    ].join("\n"))
    const anomalies = await evaluateComposerContinuationCues(dir)
    expect(anomalies.map((item) => item.id)).toContain("missing-resume-sessions")
    expect(anomalies.map((item) => item.id)).toContain("missing-attach")
    expect(anomalies.map((item) => item.id)).toContain("missing-ctrl-k-model")
    expect(anomalies.map((item) => item.id)).toContain("legacy-generic-cues-visible")
    await rm(dir, { recursive: true, force: true })
  })
})
