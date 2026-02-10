import React from "react"
import { render } from "ink-testing-library"
import { promises as fs } from "node:fs"
import path from "node:path"
import stripAnsi from "strip-ansi"
import { ReplView, type ConversationEntry, type StreamStats } from "../src/commands/repl.js"

const buildSample = () => {
  const conversation: ConversationEntry[] = [
    { id: "conv-a", speaker: "user", text: "List the files in this directory.", phase: "final", createdAt: 1 },
    { id: "conv-b", speaker: "assistant", text: "Sure — running list_dir.", phase: "final", createdAt: 2 },
  ]
  const toolEvents = [
    { id: "tool-1", kind: "call", text: "List(./)", status: "pending", createdAt: 3 },
    { id: "tool-2", kind: "result", text: "List(./)\nFound 3 files", status: "success", createdAt: 4 },
  ]
  const stats: StreamStats = {
    eventCount: 6,
    toolCount: 2,
    lastTurn: 1,
    remote: true,
    model: "openai/gpt-5.2",
  }
  return { conversation, toolEvents, stats }
}

const renderForWidth = async (width: number, outDir: string) => {
  const { conversation, toolEvents, stats } = buildSample()
  const { lastFrame, unmount } = render(
    <ReplView
      sessionId="sess-matrix"
      conversation={conversation}
      toolEvents={toolEvents}
      liveSlots={[]}
      status="Ready"
      pendingResponse={false}
      disconnected={false}
      hints={[]}
      stats={stats}
      modelMenu={{ status: "hidden" }}
      skillsMenu={{ status: "hidden" }}
      inspectMenu={{ status: "hidden" }}
      guardrailNotice={null}
      viewClearAt={null}
      viewPrefs={{ collapseMode: "auto", virtualization: "auto", richMarkdown: false }}
      todos={[]}
      tasks={[]}
      permissionRequest={null}
      permissionError={null}
      permissionQueueDepth={0}
      rewindMenu={{ status: "hidden" }}
      onSubmit={async () => {}}
      onModelMenuOpen={async () => {}}
      onModelSelect={async () => {}}
      onModelMenuCancel={() => {}}
      onSkillsMenuOpen={async () => {}}
      onSkillsMenuCancel={() => {}}
      onSkillsApply={async () => {}}
      onGuardrailToggle={() => {}}
      onGuardrailDismiss={() => {}}
      onPermissionDecision={async () => {}}
      onRewindClose={() => {}}
      onRewindRestore={async () => {}}
      onCtreeRequest={async () => {}}
      onListFiles={async () => []}
      onReadFile={async (path) => ({ path, content: "", truncated: false, total_bytes: 0 })}
    />,
    { width },
  )
  const frame = stripAnsi(lastFrame() ?? "")
  unmount()
  const outPath = path.join(outDir, `transcript_width_${width}.txt`)
  await fs.writeFile(outPath, frame, "utf8")
  return outPath
}

const main = async () => {
  const outDir = path.resolve("docs_tmp/tui_design/goldens_phase1")
  await fs.mkdir(outDir, { recursive: true })
  const widths = [80, 100, 120]
  const outputs: string[] = []
  for (const width of widths) {
    outputs.push(await renderForWidth(width, outDir))
  }
  console.log(`[render_repl_width_matrix] wrote ${outputs.join(", ")}`)
}

main().catch((error) => {
  console.error("[render_repl_width_matrix] failed:", error)
  process.exit(1)
})

