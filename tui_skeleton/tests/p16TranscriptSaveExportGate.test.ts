import { afterEach, describe, expect, it } from "vitest"
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"

import { evaluateP16TranscriptSaveExport } from "../scripts/p16_transcript_save_export_gate.js"

describe("P16 transcript save/export gate", () => {
  let root: string | null = null

  afterEach(async () => {
    if (root) await rm(root, { recursive: true, force: true })
    root = null
  })

  const makeCase = async (exportText: string) => {
    root = await mkdtemp(path.join(os.tmpdir(), "p16-transcript-export-"))
    const caseDir = path.join(root, "docs_tmp", "cli_phase_6", "case")
    const transcriptDir = path.join(root, "tui_skeleton", "artifacts", "transcripts")
    await mkdir(caseDir, { recursive: true })
    await mkdir(transcriptDir, { recursive: true })
    await writeFile(
      path.join(caseDir, "pty_snapshots.txt"),
      [
        "# p16-transcript-viewer",
        "breadboard transcript viewer",
        "❯ Hello viewer",
        "Implementation receipts",
        "# p16-save-feedback",
        "Saved to /tmp/transcript.txt",
        "# p16-after-transcript-return",
        "❯ Type your request…",
      ].join("\n"),
      "utf8",
    )
    await writeFile(path.join(caseDir, "pty_raw.ansi"), "Saved to /tmp/transcript.txt", "utf8")
    await writeFile(path.join(caseDir, "repl_state.ndjson"), '{"state":{"preview":"Hello viewer Implementation receipts"}}\n', "utf8")
    await writeFile(path.join(transcriptDir, "transcript-test.txt"), exportText, "utf8")
    return caseDir
  }

  it("accepts saved transcript content without internal prompt leaks", async () => {
    const caseDir = await makeCase("❯ Hello viewer\n\nImplementation receipts and verification receipts are present.\n")
    await expect(evaluateP16TranscriptSaveExport(caseDir)).resolves.toEqual([])
  })

  it("flags system prompt leakage in exported content", async () => {
    const caseDir = await makeCase("❯ Hello viewer\n\nImplementation receipts\n\nYou are Codex, based on GPT-5.\n")
    await expect(evaluateP16TranscriptSaveExport(caseDir)).resolves.toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "export-leak" })]),
    )
  })
})
