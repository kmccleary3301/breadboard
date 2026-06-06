import { mkdir, mkdtemp, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { evaluateScrollbackGhosttyHostHistoryPreservation } from "../tools/assertions/scrollbackGhosttyHostHistoryPreservationCheck.js"

const makeCase = async (files: Record<string, string>): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-ghostty-history-check-"))
  const observer = path.join(dir, "observer_text")
  const terminal = path.join(dir, "terminal_text")
  await mkdir(observer, { recursive: true })
  await mkdir(terminal, { recursive: true })
  await writeFile(path.join(dir, "app_start_anchor.txt"), '{"mode":"preserved-scrollback","preAppHistoryPolicy":"untouched"}\n', "utf8")
  await writeFile(path.join(dir, "managed_region_bounds.ndjson"), '{"event":"managed_region_bounds"}\n', "utf8")
  await writeFile(path.join(dir, "scrollback_clause_verdicts.json"), '{"verdicts":[]}\n', "utf8")
  await writeFile(path.join(terminal, "scrollback_final.txt"), files["terminal_text/scrollback_final.txt"] ?? "", "utf8")
  for (const [name, body] of Object.entries(files)) {
    if (name.startsWith("terminal_text/")) continue
    await writeFile(path.join(observer, name), body, "utf8")
  }
  return dir
}

describe("scrollbackGhosttyHostHistoryPreservationCheck", () => {
  it("accepts seeded host history from Ghostty-native scrollback exports", async () => {
    const dir = await makeCase({
      "terminal_text/scrollback_final.txt": "label: final\n",
      "streaming-final.ghostty_scrollback.txt": "PRE_APP_ALPHA\nPRE_APP_BETA\n# Streaming\n",
    })

    await expect(evaluateScrollbackGhosttyHostHistoryPreservation(dir)).resolves.toEqual([])
  })

  it("accepts seeded host history from Ghostty-native screen exports when native scrollback only contains offscreen rows", async () => {
    const dir = await makeCase({
      "terminal_text/scrollback_final.txt": "label: final\n",
      "streaming-final.ghostty_scrollback.txt": "PRE_APP_ALPHA\n",
      "streaming-final.ghostty_screen.txt": "PRE_APP_BETA\nBreadBoard v0.2.0\n",
    })

    await expect(evaluateScrollbackGhosttyHostHistoryPreservation(dir)).resolves.toEqual([])
  })

  it("still flags missing pre-app history when neither native nor tmux captures contain it", async () => {
    const dir = await makeCase({
      "terminal_text/scrollback_final.txt": "# Streaming\n",
      "streaming-final.ghostty_scrollback.txt": "assistant content only\n",
    })

    const anomalies = await evaluateScrollbackGhosttyHostHistoryPreservation(dir)
    expect(anomalies.map((entry) => entry.id)).toEqual(["pre-app-history-missing"])
  })

  it("rejects native-claim cases that only have fallback terminal text and no Ghostty-native exports", async () => {
    const dir = await makeCase({
      "terminal_text/scrollback_final.txt": "PRE_APP_ALPHA\nPRE_APP_BETA\nx11 tree fallback\n",
      "streaming-final.x11.txt": "0x600004 \"ghostty\"\n",
      "streaming-final.summary.txt": "ghostty_native_export_status: screen=missing scrollback=missing\n",
    })

    const anomalies = await evaluateScrollbackGhosttyHostHistoryPreservation(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("ghostty-native-export-missing")
  })
})
