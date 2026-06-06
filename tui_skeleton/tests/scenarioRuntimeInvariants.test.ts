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

})
