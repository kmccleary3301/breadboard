import { promises as fs } from "node:fs"
import path from "node:path"
import type { ReplState } from "../src/commands/repl/controller.js"
import { renderStateToText } from "../src/commands/repl/renderText.js"

const OUTPUT_COMPACT = path.join("scripts", "log_window_compact.txt")
const OUTPUT_FULL = path.join("scripts", "log_window_full.txt")

const buildState = (virtualization: "auto" | "compact"): ReplState => {
  const entries = Array.from({ length: 250 }).map((_, index) => ({
    id: `conv-${index}`,
    speaker: index % 2 === 0 ? ("assistant" as const) : ("user" as const),
    text: `Turn ${index + 1}: ${index % 2 === 0 ? "assistant reply" : "user prompt"} `,
    phase: "final" as const,
  }))
  const state: ReplState = {
    sessionId: "log-window-demo",
    status: "Ready",
    pendingResponse: false,
    conversation: entries,
    toolEvents: [],
    liveSlots: [],
    hints: [],
    stats: { eventCount: entries.length, toolCount: 0, lastTurn: entries.length, remote: false, model: "mock/demo" },
    modelMenu: { status: "hidden" },
    completionReached: true,
    completionSeen: true,
    lastCompletion: { completed: true, summary: null },
    disconnected: false,
    guardrailNotice: null,
    viewPrefs: { collapseMode: "auto", virtualization },
  }
  return state
}

const writeSnapshot = async (filePath: string, text: string) => {
  await fs.writeFile(filePath, text, "utf8")
  console.log(`[log-window] Wrote ${filePath}`)
}

const main = async () => {
  const options = { includeStatus: false, includeHeader: false }
  const compact = renderStateToText(buildState("compact"), options)
  const full = renderStateToText(buildState("auto"), options)
  await writeSnapshot(OUTPUT_COMPACT, compact)
  await writeSnapshot(OUTPUT_FULL, full)
}

main().catch((error) => {
  console.error("[log-window] Failed to generate fixture:", error)
  process.exit(1)
})
