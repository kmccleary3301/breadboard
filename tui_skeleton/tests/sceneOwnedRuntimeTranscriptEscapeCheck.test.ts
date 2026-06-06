import { describe, expect, it } from "vitest"
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateSceneOwnedRuntimeTranscriptEscape } from "../tools/assertions/sceneOwnedRuntimeTranscriptEscapeCheck.ts"

const writeCase = async (viewerOpen: string, viewerBack: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-scene-owned-transcript-"))
  const observerTextDir = path.join(dir, "observer_text")
  await mkdir(observerTextDir, { recursive: true })
  await writeFile(path.join(observerTextDir, "viewer-open.txt"), viewerOpen, "utf8")
  await writeFile(path.join(observerTextDir, "viewer-back.txt"), viewerBack, "utf8")
  return dir
}

describe("evaluateSceneOwnedRuntimeTranscriptEscape", () => {
  it("accepts restored scene host and transcript affordance", async () => {
    const dir = await writeCase(
      "follow tail · g top\n",
      [
        "Live Shell · Ready",
        "❯ stream markdown",
        "resume /sessions · @ attach · ctrl+o transcript · ctrl+k model",
        "",
      ].join("\n"),
    )
    await expect(evaluateSceneOwnedRuntimeTranscriptEscape(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags reset or missing transcript affordance", async () => {
    const dir = await writeCase(
      "follow tail\n",
      [
        "Live Shell · Ready",
        "No conversation yet. Type a prompt to get started.",
        "footer",
        "",
      ].join("\n"),
    )
    const anomalies = await evaluateSceneOwnedRuntimeTranscriptEscape(dir)
    expect(anomalies.map((item) => item.id)).toContain("transcript-open-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("transcript-return-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("transcript-return-reset-empty")
    expect(anomalies.map((item) => item.id)).toContain("transcript-return-context-missing")
    await rm(dir, { recursive: true, force: true })
  })
})
