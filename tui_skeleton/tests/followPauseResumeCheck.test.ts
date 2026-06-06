import { describe, expect, it } from "vitest"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { evaluateFollowPauseResume } from "../tools/assertions/followPauseResumeCheck.ts"

const makeCaseDir = () => mkdtempSync(path.join(os.tmpdir(), "follow-pause-resume-"))

describe("evaluateFollowPauseResume", () => {
  it("accepts a paused-then-resumed streaming case", async () => {
    const caseDir = makeCaseDir()
    try {
      writeFileSync(
        path.join(caseDir, "pty_snapshots.txt"),
        [
          "# before-pause",
          "## Pause Follow",
          "- first",
          "# paused",
          "[responding] follow paused",
          "## Pause Follow",
          "- first",
          "# paused-after-tail-advance",
          "[responding] follow paused",
          "## Pause Follow",
          "- first",
          "# after-resume",
          "## Pause Follow",
          "- first",
          "- second",
          "- third",
          "Final.",
          "# settled",
          "## Pause Follow",
          "- first",
          "- second",
          "- third",
          "Final.",
          "[ready]",
          "",
        ].join("\n"),
      )
      writeFileSync(
        path.join(caseDir, "repl_state.ndjson"),
        [
          JSON.stringify({ state: { pendingResponse: true, mainFollowTail: true } }),
          JSON.stringify({ state: { pendingResponse: true, mainFollowTail: false } }),
          JSON.stringify({ state: { pendingResponse: true, mainFollowTail: true } }),
        ].join("\n"),
      )
      writeFileSync(
        path.join(caseDir, "surface_model.ndjson"),
        [
          JSON.stringify({ pendingResponse: true, mainFollowTail: false, transcriptTailCount: 2 }),
          JSON.stringify({ pendingResponse: true, mainFollowTail: true, transcriptTailCount: 1 }),
        ].join("\n"),
      )

      await expect(evaluateFollowPauseResume(caseDir)).resolves.toEqual([])
    } finally {
      rmSync(caseDir, { recursive: true, force: true })
    }
  })
})
