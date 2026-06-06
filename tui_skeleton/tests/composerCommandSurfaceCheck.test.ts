import { afterEach, describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"

import { evaluateComposerCommandSurface } from "../tools/assertions/composerCommandSurfaceCheck.js"

describe("evaluateComposerCommandSurface", () => {
  let caseDir: string | null = null

  afterEach(async () => {
    if (caseDir) await rm(caseDir, { recursive: true, force: true })
    caseDir = null
  })

  it("accepts the continuation command surface snapshots", async () => {
    caseDir = await mkdtemp(path.join(os.tmpdir(), "composer-command-surface-"))
    await writeFile(
      path.join(caseDir, "pty_snapshots.txt"),
      [
        "# slash-default-suggestions",
        "/resume  Open recent-session re-entry overlay.",
        "/transcript  Open the transcript viewer.",
        "/attach  Start the @ attach/file picker.",
        "/models  Open interactive model picker.",
        "/shortcuts  Open the shortcuts sheet.",
        "# transcript-open",
        "follow tail · g top",
        "# sessions-open",
        "Enter attach",
        "11 sessions • lines 1-11",
      ].join("\n"),
      "utf8",
    )

    await expect(evaluateComposerCommandSurface(caseDir)).resolves.toEqual([])
  })

  it("flags missing continuation commands and overlays", async () => {
    caseDir = await mkdtemp(path.join(os.tmpdir(), "composer-command-surface-"))
    await writeFile(
      path.join(caseDir, "pty_snapshots.txt"),
      [
        "# slash-default-suggestions",
        "/help  Show available slash commands.",
        "/quit  Exit the session.",
        "# transcript-open",
        "not here",
        "# sessions-open",
        "no session overlay",
      ].join("\n"),
      "utf8",
    )

    await expect(evaluateComposerCommandSurface(caseDir)).resolves.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "missing-resume" }),
        expect.objectContaining({ id: "missing-transcript" }),
        expect.objectContaining({ id: "missing-attach" }),
        expect.objectContaining({ id: "missing-models" }),
        expect.objectContaining({ id: "missing-shortcuts" }),
        expect.objectContaining({ id: "legacy-default-suggestions-visible" }),
        expect.objectContaining({ id: "transcript-open-missing" }),
        expect.objectContaining({ id: "sessions-overlay-missing" }),
      ]),
    )
  })
})
