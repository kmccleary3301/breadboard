import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { evaluateInvariants } from "../tools/scenario-runtime/invariants/invariantRegistry"

const withArtifactDir = async (fn: (dir: string) => Promise<void>) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-scenario-invariant-"))
  try {
    await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

describe("scenario runtime invariants", () => {
  it("fails missing host history sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ hostHistorySentinels: ["BB_PRE_HISTORY_SENTINEL"] }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "BreadBoard v0.2.0\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_host_history", "pty", [{ id: "GLOBAL-HOST-HISTORY-PRESERVED", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails host history sentinels after launch markers", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ hostHistorySentinels: ["BB_PRE_HISTORY_SENTINEL"] }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "BreadBoard v0.2.0\nBB_PRE_HISTORY_SENTINEL\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_host_history_order", "pty", [{ id: "SCROLL-HOST-HISTORY-PREFIX-PRESERVED", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails alternate-screen entry in normal scrollback mode", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "pty_raw.ansi"), "before\x1b[?1049hafter", "utf8")
      const report = await evaluateInvariants(dir, "bad_alt_screen", "pty", [{ id: "SCROLL-NO-ALTERNATE-SCREEN-HIJACK", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails missing resize action confirmation", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "action_confirmations.json"), JSON.stringify({ resizeRequested: true, resizeObserved: false }), "utf8")
      const report = await evaluateInvariants(dir, "bad_resize", "pty", [{ id: "RESIZE-ACTION-OBSERVED", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails system prompt leakage", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "You are Codex, based on GPT-5", "utf8")
      const report = await evaluateInvariants(dir, "bad_prompt", "pty", [{ id: "GLOBAL-NO-SYSTEM-PROMPT-LEAK", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails MaxListenersExceededWarning leakage", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "pty_raw.ansi"), "(node:123) MaxListenersExceededWarning: Possible EventEmitter memory leak detected\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_max_listeners", "pty", [{ id: "GLOBAL-NO-MAX-LISTENERS-WARNING", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails retry attempt labels that exceed their budget", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "Engine interrupted: terminated. Restarting owned engine (attempt 6/5).\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_retry_budget", "pty", [{ id: "GLOBAL-NO-RETRY-BUDGET-OVERFLOW", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({ overflow: [{ value: 6, budget: 5 }] })
    })
  })

  it("passes retry attempt labels within their budget", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "Stream interruption: terminated. Retrying in 4000ms (attempt 4/5).\n", "utf8")
      const report = await evaluateInvariants(dir, "good_retry_budget", "pty", [{ id: "GLOBAL-NO-RETRY-BUDGET-OVERFLOW", severity: "blocker" }])
      expect(report.ok).toBe(true)
      expect(report.results[0]?.status).toBe("pass")
    })
  })

  it("fails duplicated landing pane markers", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "scrollback_final.txt"), "BreadBoard v0.2.0\nUsing Config `Codex`\nBreadBoard v0.2.0\nUsing Config `Codex`\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_landing_cardinality", "pty", [{ id: "SCROLL-LANDING-CARDINALITY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails prompt cardinality mismatch", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedPrompt: "Show me markdown." }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "Show me markdown.\nShow me markdown.\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_prompt_cardinality", "pty", [{ id: "SCROLL-PROMPT-CARDINALITY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails missing expected assistant terms", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedAssistantText: "Final assistant answer complete." }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "Final assistant\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_assistant_cardinality", "pty", [{ id: "SCROLL-ASSISTANT-CARDINALITY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("accepts rendered markdown terms for transcript order", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedPrompt: "Show markdown.", expectedAssistantText: "## Rendered Heading\n- durable item" }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "❯ Show markdown.\n\nRendered Heading\n\n- durable item\n", "utf8")
      const report = await evaluateInvariants(dir, "rendered_markdown_order", "pty", [{ id: "GLOBAL-TRANSCRIPT-ORDER", severity: "blocker" }])
      expect(report.ok).toBe(true)
      expect(report.results[0]?.status).toBe("pass")
    })
  })

  it("fails destructive clear-scrollback sequences", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "pty_raw.ansi"), "before\x1b[2J\x1b[3J\x1b[Hafter", "utf8")
      const report = await evaluateInvariants(dir, "bad_clear", "pty", [{ id: "SCROLL-NO-DESTRUCTIVE-CLEAR", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails duplicate assistant tail lines", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedAssistantText: "## Header\nFinal assistant sentence." }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "Final assistant sentence.\nFinal assistant sentence.\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_duplicate_tail", "pty", [{ id: "SCROLL-NO-DUPLICATE-ASSISTANT-TAIL", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails replayed tool blocks in scrollback", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({ state: { toolEvents: [{ kind: "result", status: "success" }] } })}\n`,
        "utf8",
      )
      await writeFile(path.join(dir, "action_confirmations.json"), JSON.stringify({ toolCompleted: true }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "● Tool\n● Tool\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_duplicate_tool", "pty", [{ id: "TOOL-RESULT-SINGULAR", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.message).toContain("tool block cardinality")
    })
  })
  it("fails streaming markdown partial replay", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedAssistantText: "## Streaming Markdown\n\n| key | value |\n| --- | --- |\n| status | ok |" }), "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "# Streaming Markdown\n# Streaming Markdown\n# Streaming Markdown\n┌────┬────┐\n┌────┬────┐\n┌────┬────┐\n┌────┬────┐\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_markdown_replay", "pty", [{ id: "MD-NO-STREAMING-PARTIAL-REPLAY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails replayed input separators", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "scrollback_final.txt"), "— input\n— input\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_input_replay", "pty", [{ id: "SCROLL-NO-INPUT-SEPARATOR-REPLAY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails replayed composer prompts", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "scrollback_final.txt"), " ❯   Type your request…\n\n ❯   Type your request…\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_composer_prompt_replay", "pty", [{ id: "SCROLL-NO-COMPOSER-PROMPT-REPLAY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails guardrail chrome in scrollback body", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "scrollback_final.txt"), "│  ✗ Guardrail: Error: synthetic fault │\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_guardrail_body", "pty", [{ id: "SCROLL-NO-CONTROL-CHROME-BODY", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails missing composer affordance", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "BreadBoard v0.2.0\nNo input affordance here\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_missing_composer", "pty", [{ id: "GLOBAL-COMPOSER-VISIBLE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails missing truthful footer affordance", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "BreadBoard v0.2.0\n❯ Type your request…\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_missing_footer", "pty", [{ id: "GLOBAL-FOOTER-TRUTHFUL", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails missing declared visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedVisibleText: ["VISIBLE_SENTINEL_A", "VISIBLE_SENTINEL_B"] }), "utf8")
      await writeFile(path.join(dir, "viewport_final.txt"), "VISIBLE_SENTINEL_A\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_missing_visible_sentinel", "pty", [{ id: "VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails missing declared final visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedFinalVisibleText: ["FINAL_SENTINEL_A"] }), "utf8")
      await writeFile(path.join(dir, "viewport_final.txt"), "not final\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_missing_final_sentinel", "pty", [{ id: "FINAL-VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("uses final viewport before stale scrollback for final visible text", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedFinalVisibleText: ["FINAL_SENTINEL_A"] }), "utf8")
      await writeFile(path.join(dir, "viewport_final.txt"), "actual final viewport\n", "utf8")
      await writeFile(path.join(dir, "scrollback_final.txt"), "stale FINAL_SENTINEL_A scrollback\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_final_viewport_masked_by_scrollback", "pty", [{ id: "FINAL-VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails duplicated declared final visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedFinalUniqueText: ["FINAL_SENTINEL_A"] }), "utf8")
      await writeFile(path.join(dir, "viewport_final.txt"), "FINAL_SENTINEL_A\nFINAL_SENTINEL_A\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_duplicate_final_sentinel", "pty", [{ id: "FINAL-VISIBLE-TEXT-SENTINELS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("passes declared snapshot visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedSnapshotVisibleText: { compact: ["SNAP_A", "SNAP_B"] } }), "utf8")
      await writeFile(path.join(dir, "terminal_grid.ndjson"), JSON.stringify({ snapshots: [{ label: "compact", cleaned: "SNAP_A\nSNAP_B\nenter send" }] }), "utf8")
      const report = await evaluateInvariants(dir, "good_snapshot_sentinels", "pty", [{ id: "SNAPSHOT-VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(true)
      expect(report.results[0]?.status).toBe("pass")
    })
  })

  it("fails missing declared snapshot visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedSnapshotVisibleText: { compact: ["SNAP_A", "SNAP_B"] } }), "utf8")
      await writeFile(path.join(dir, "terminal_grid.ndjson"), JSON.stringify({ snapshots: [{ label: "compact", cleaned: "SNAP_A\nenter send" }] }), "utf8")
      const report = await evaluateInvariants(dir, "bad_snapshot_sentinels", "pty", [{ id: "SNAPSHOT-VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({ missingSentinels: [{ label: "compact", sentinel: "SNAP_B" }] })
    })
  })

  it("fails missing declared snapshot labels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedSnapshotVisibleText: { compact: ["SNAP_A"] } }), "utf8")
      await writeFile(path.join(dir, "terminal_grid.ndjson"), JSON.stringify({ snapshots: [{ label: "wide", cleaned: "SNAP_A" }] }), "utf8")
      const report = await evaluateInvariants(dir, "bad_missing_snapshot_label", "pty", [{ id: "SNAPSHOT-VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({ missingSnapshots: ["compact"] })
    })
  })

  it("fails missing snapshot artifacts for declared snapshot sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedSnapshotVisibleText: { compact: ["SNAP_A"] } }), "utf8")
      const report = await evaluateInvariants(dir, "bad_missing_snapshot_artifact", "pty", [{ id: "SNAPSHOT-VISIBLE-TEXT-SENTINELS-PRESENT", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("passes unique declared snapshot visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedSnapshotUniqueText: { compact: ["SNAP_A", "SNAP_B"] } }), "utf8")
      await writeFile(path.join(dir, "terminal_grid.ndjson"), JSON.stringify({ snapshots: [{ label: "compact", cleaned: "SNAP_A\nSNAP_B\nenter send" }] }), "utf8")
      const report = await evaluateInvariants(dir, "good_snapshot_unique_sentinels", "pty", [{ id: "SNAPSHOT-VISIBLE-TEXT-SENTINELS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(true)
      expect(report.results[0]?.status).toBe("pass")
    })
  })

  it("fails duplicated declared snapshot visible text sentinels", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedSnapshotUniqueText: { compact: ["SNAP_A"] } }), "utf8")
      await writeFile(path.join(dir, "terminal_grid.ndjson"), JSON.stringify({ snapshots: [{ label: "compact", cleaned: "SNAP_A\nSNAP_A\nenter send" }] }), "utf8")
      const report = await evaluateInvariants(dir, "bad_snapshot_duplicate_sentinel", "pty", [{ id: "SNAPSHOT-VISIBLE-TEXT-SENTINELS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({ nonUnique: [{ label: "compact", sentinel: "SNAP_A", count: 2 }] })
    })
  })

  it("fails visible bracketed paste markers", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "❯ [200~pasted text[201~\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_paste_marker", "pty", [{ id: "COMPOSER-NO-BRACKETED-PASTE-MARKERS", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails duplicate footer status lines", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "• [ready] last 1s · enter send\n• [ready] last 1s · enter send\nresume /sessions · ctrl+o transcript\nresume /sessions · ctrl+o transcript\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_footer_duplicate", "pty", [{ id: "FOOTER-NO-DUPLICATE-STATUS", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails ready-lie lifecycle state", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "state_dumps.ndjson"), `${JSON.stringify({ state: { status: "[ready] enter send", disconnected: true, pendingResponse: false } })}\n`, "utf8")
      const report = await evaluateInvariants(dir, "bad_ready_lie", "pty", [{ id: "GLOBAL-NO-READY-LIE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails stale lifecycle border fragments", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "viewport_final.txt"), "Disconnected: Lost connection to the engine.\n│\n╰────────\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_stale_border", "pty", [{ id: "RESIZE-NO-STALE-BORDER", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails lifecycle recovery body pollution", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "scrollback_final.txt"), "User prompt\nDisconnected: Lost connection to the engine.\nAssistant answer\n", "utf8")
      const report = await evaluateInvariants(dir, "bad_lifecycle_pollution", "pty", [{ id: "LIFE-RECOVERY-NO-BODY-POLLUTION", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails stale streaming rows after idle", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: {
            pendingResponse: false,
            conversation: [{ id: "conv-live", speaker: "assistant", phase: "streaming", text: "still streaming" }],
            liveSlots: [],
          },
          transcriptCells: [{ id: "msg:conv-live", lifecycle: "live", streaming: true, phase: "streaming" }],
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_stale_streaming_idle", "pty", [{ id: "LIVE-NO-STALE-STREAMING-WHEN-IDLE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails active live slots after idle", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({ state: { pendingResponse: false, conversation: [], liveSlots: [{ id: "tool-1", status: "running", text: "tool running" }] } })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_pending_slot_idle", "pty", [{ id: "LIVE-NO-PENDING-SLOTS-WHEN-IDLE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails duplicate transcript cell IDs", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({ state: { pendingResponse: false, conversation: [], liveSlots: [] }, transcriptCells: [{ id: "msg:1" }, { id: "msg:1" }] })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_duplicate_cell_ids", "pty", [{ id: "LIVE-TRANSCRIPT-CELL-IDS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails duplicate durable dedupe keys", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: { pendingResponse: false, conversation: [], liveSlots: [] },
          transcriptCells: [
            { id: "sys:1", lifecycle: "committed", streaming: false, dedupeKey: "diag:provider-auth" },
            { id: "sys:2", lifecycle: "committed", streaming: false, dedupeKey: "diag:provider-auth" },
          ],
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_duplicate_dedupe_keys", "pty", [{ id: "LIVE-DURABLE-DEDUPE-KEYS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("fails duplicate durable prompt semantic IDs", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: {
            pendingResponse: false,
            conversation: [
              { id: "conv-user-1", speaker: "user", phase: "final", text: "Hello" },
              { id: "conv-user-1", speaker: "user", phase: "final", text: "Hello" },
            ],
            liveSlots: [],
          },
          transcriptCells: [
            { id: "msg:conv-user-1", role: "user-request", lifecycle: "committed", streaming: false },
            { id: "msg:conv-user-1", role: "user-request", lifecycle: "committed", streaming: false },
          ],
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_duplicate_prompt_ids", "pty", [{ id: "DURABLE-PROMPT-IDS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({
        duplicateConversationIds: ["conv-user-1"],
        duplicateCellIds: ["msg:conv-user-1"],
      })
    })
  })

  it("fails duplicate durable assistant final semantic IDs", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: {
            pendingResponse: false,
            conversation: [
              { id: "conv-assistant-1", speaker: "assistant", phase: "final", text: "Done" },
              { id: "conv-assistant-1", speaker: "assistant", phase: "final", text: "Done" },
            ],
            liveSlots: [],
          },
          transcriptCells: [
            { id: "msg:conv-assistant-1", role: "assistant-message", lifecycle: "committed", streaming: false },
            { id: "msg:conv-assistant-1", role: "assistant-message", lifecycle: "committed", streaming: false },
          ],
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_duplicate_assistant_ids", "pty", [{ id: "DURABLE-ASSISTANT-FINAL-IDS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({
        duplicateConversationIds: ["conv-assistant-1"],
        duplicateCellIds: ["msg:conv-assistant-1"],
      })
    })
  })

  it("fails duplicate durable tool semantic IDs", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: {
            pendingResponse: false,
            conversation: [],
            toolEvents: [
              { id: "slot-a", kind: "result", status: "success", callId: "tool-call-1" },
              { id: "slot-b", kind: "result", status: "success", callId: "tool-call-1" },
            ],
            liveSlots: [],
          },
          transcriptCells: [
            { id: "tool:tool-call-1", kind: "tool", role: "tool-result", lifecycle: "committed", streaming: false },
            { id: "tool:tool-call-1", kind: "tool", role: "tool-result", lifecycle: "committed", streaming: false },
          ],
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_duplicate_tool_ids", "pty", [{ id: "DURABLE-TOOL-IDS-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({
        duplicateToolEventIds: ["tool-call-1"],
        duplicateCellIds: ["tool:tool-call-1"],
      })
    })
  })

  it("fails duplicate recovery episode lines in latest state", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: {
            pendingResponse: false,
            conversation: [],
            liveSlots: [],
            hints: [
              "Engine interrupted: terminated. Restarting owned engine (attempt 1/5).",
              "Engine interrupted: terminated. Restarting owned engine (attempt 1/5).",
            ],
          },
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_duplicate_recovery_episode", "pty", [{ id: "DURABLE-RECOVERY-EPISODES-UNIQUE", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
      expect(report.results[0]?.evidence).toMatchObject({
        duplicates: ["Engine interrupted: terminated. Restarting owned engine (attempt 1/5)."],
      })
    })
  })

  it("fails overlay-only durable transcript mutation", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({
          state: {
            pendingResponse: false,
            conversation: [{ id: "conv-overlay", speaker: "system", text: "unexpected overlay transcript row", phase: "final" }],
            toolEvents: [],
          },
          transcriptCells: [{ id: "msg:conv-overlay", kind: "message", source: "conversation" }],
        })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "bad_overlay_mutation", "pty", [{ id: "OVERLAY-NO-DURABLE-MUTATION", severity: "blocker" }])
      expect(report.ok).toBe(false)
      expect(report.results[0]?.status).toBe("fail")
    })
  })

  it("skips overlay-only durable mutation check after a submitted prompt", async () => {
    await withArtifactDir(async (dir) => {
      await writeFile(path.join(dir, "manifest.json"), JSON.stringify({ expectedPrompt: "Submitted turn." }), "utf8")
      await writeFile(
        path.join(dir, "state_dumps.ndjson"),
        `${JSON.stringify({ state: { pendingResponse: false, conversation: [{ id: "conv-1", speaker: "user", text: "Submitted turn.", phase: "final" }], toolEvents: [] } })}\n`,
        "utf8",
      )
      const report = await evaluateInvariants(dir, "submitted_overlay_skip", "pty", [{ id: "OVERLAY-NO-DURABLE-MUTATION", severity: "blocker" }])
      expect(report.ok).toBe(true)
      expect(report.results[0]?.status).toBe("skip")
    })
  })

})
