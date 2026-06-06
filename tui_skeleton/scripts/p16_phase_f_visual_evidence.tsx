import React from "react"
import { render } from "ink-testing-library"
import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import stripAnsi from "strip-ansi"

import { ReplView } from "../src/repl/components/ReplView.js"
import type {
  ConversationEntry,
  ModelMenuItem,
  PermissionRequest,
  StreamStats,
  TaskEntry,
  TodoStoreSnapshot,
  ToolLogEntry,
  WorkGraphState,
} from "../src/repl/types.js"
import type { ReplViewProps } from "../src/repl/components/replView/replViewTypes.js"
import { DEFAULT_RESOLVED_TUI_CONFIG } from "../src/tui_config/presets.js"
import { stripAnsiCodes, visibleWidth } from "../src/repl/components/replView/utils/ansi.js"

const __filename = fileURLToPath(import.meta.url)
const ROOT_DIR = path.resolve(path.dirname(__filename), "..")
const REPO_DIR = path.resolve(ROOT_DIR, "..")
const P16_DIR = path.resolve(
  REPO_DIR,
  "docs_tmp",
  "cli_phase_6",
  "CODESIGN_p16",
  "implementation_validation_p16_final_design_complete",
)
const PHASE_F_DIR = path.join(P16_DIR, "artifacts", "tools_permissions_diff")

const flush = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms))

const timestamp = (): string => {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

const escapeXml = (value: string): string =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")

const run = (command: string, args: readonly string[], cwd = REPO_DIR): Promise<{ code: number; stdout: string; stderr: string }> =>
  new Promise((resolve) => {
    const child = spawn(command, [...args], { cwd, stdio: ["ignore", "pipe", "pipe"] })
    const stdout: Buffer[] = []
    const stderr: Buffer[] = []
    child.stdout.on("data", (chunk) => stdout.push(Buffer.from(chunk)))
    child.stderr.on("data", (chunk) => stderr.push(Buffer.from(chunk)))
    child.on("error", (error) => resolve({ code: 127, stdout: "", stderr: String(error) }))
    child.on("exit", (code) =>
      resolve({ code: code ?? 1, stdout: Buffer.concat(stdout).toString("utf8"), stderr: Buffer.concat(stderr).toString("utf8") }),
    )
  })

const runConvert = async (svgPath: string, pngPath: string): Promise<boolean> => {
  const result = await run("convert", [svgPath, pngPath], REPO_DIR)
  return result.code === 0
}

type SurfaceId =
  | "tool_long_output"
  | "tool_final_states"
  | "permission_summary"
  | "permission_diff"
  | "permission_rules"
  | "diff_command"

type SurfaceCase = {
  readonly surface: SurfaceId
  readonly label: string
  readonly width: number
  readonly frame: string
  readonly artifactKind: "production_repl_controller"
  readonly interactionPath: string
  readonly expectedMarkers: readonly string[]
}

type SurfaceReport = {
  readonly surface: SurfaceId
  readonly label: string
  readonly width: number
  readonly artifactKind: string
  readonly interactionPath: string
  readonly lineCount: number
  readonly maxVisibleWidth: number
  readonly markersPass: boolean
  readonly widthPass: boolean
  readonly forbiddenPass: boolean
  readonly expectedMarkers: readonly string[]
  readonly missingMarkers: readonly string[]
  readonly forbiddenHits: readonly string[]
  readonly frame: string
}

const baseStats: StreamStats = {
  eventCount: 43,
  toolCount: 6,
  lastTurn: 7,
  remote: false,
  model: "gpt-5.4-mini",
  usage: { promptTokens: 4211, completionTokens: 987, totalTokens: 5198, latencyMs: 3270 },
}

const now = Date.now()

const todoStore: TodoStoreSnapshot = { revision: 0, updatedAt: now, itemsById: {}, order: [] }
const workGraph: WorkGraphState = { itemsById: {}, itemOrder: [], lanesById: {}, laneOrder: [], processedEventKeys: [], lastSeq: 0 }

const modelItems: readonly ModelMenuItem[] = [
  { label: "gpt-5.4-mini", value: "gpt-5.4-mini", provider: "openai", detail: "default Codex E4 lane", isCurrent: true, contextTokens: 400000, priceInPerM: 0.25, priceOutPerM: 2.0 },
  { label: "gpt-5.4", value: "gpt-5.4", provider: "openai", detail: "larger planning/coding model", contextTokens: 400000, priceInPerM: 1.25, priceOutPerM: 10 },
  { label: "gpt-5.1-codex-mini", value: "gpt-5.1-codex-mini", provider: "openai", detail: "legacy Codex comparison lane", contextTokens: 200000 },
  { label: "local-qwen-coder", value: "local-qwen-coder", provider: "local", detail: "local smoke lane", contextTokens: 32768 },
]

const conversation: ConversationEntry[] = [
  { id: "u-1", speaker: "user", text: "Inspect this dummy package and explain the safest fix.", phase: "final", createdAt: now - 9000 },
  {
    id: "a-1",
    speaker: "assistant",
    text: "I found one failing path and will keep the change scoped.\n\n- The parser accepts empty hostnames.\n- The SMTP handler should reject invalid recipients.\n- I will add a narrow guard and a test.",
    phase: "final",
    createdAt: now - 8000,
  },
  { id: "u-2", speaker: "user", text: "Show me the diff and summarize the risk.", phase: "final", createdAt: now - 7000 },
  {
    id: "a-2",
    speaker: "assistant",
    text: "The patch is small. The only behavioral change is that invalid envelope recipients now fail before the message body is accepted.",
    phase: "streaming",
    createdAt: now - 6000,
    markdownStreaming: true,
  },
]

const longOutput = [
  "gcc -Wall -Wextra -pedantic smtp_server.c -o smtp_server",
  "smtp_server.c:88: note: validating HELO domain",
  "smtp_server.c:143: note: validating RCPT command",
  "tests/smtp_invalid_recipient.sh: PASS",
  "tests/smtp_basic_delivery.sh: PASS",
  "tests/smtp_quit_cleanup.sh: PASS",
  "summary: 3 passed, 0 failed",
].join("\n")

const unifiedDiff = [
  "diff --git a/smtp_server.c b/smtp_server.c",
  "index 1a2b3c4..5d6e7f8 100644",
  "--- a/smtp_server.c",
  "+++ b/smtp_server.c",
  "@@ -137,6 +137,10 @@ static int handle_rcpt(struct session *s, const char *arg) {",
  "     if (arg == NULL) {",
  "         return smtp_reply(s, 501, \"missing recipient\");",
  "     }",
  "+    if (!is_valid_mailbox(arg)) {",
  "+        return smtp_reply(s, 553, \"invalid recipient\");",
  "+    }",
  "+",
  "     return smtp_reply(s, 250, \"recipient ok\");",
  " }",
].join("\n")

const toolEvents: ToolLogEntry[] = [
  { id: "tool-call-1", kind: "call", text: "run_shell · rg -n \"handle_rcpt|is_valid_mailbox\" .", status: "pending", createdAt: now - 5000, callId: "call-1" },
  { id: "tool-result-1", kind: "result", text: longOutput, status: "success", createdAt: now - 4000, callId: "call-1" },
  { id: "tool-diff-1", kind: "result", text: unifiedDiff, status: "success", createdAt: now - 3000, callId: "call-2", diff: { kind: "diff", filePath: "smtp_server.c", unified: unifiedDiff, additions: 4, deletions: 0, language: "diff", title: "Guard invalid recipient" } },
]

const longToolOutput = Array.from({ length: 34 }, (_, index) =>
  `smtp_stress_case_${String(index + 1).padStart(2, "0")}: ${index % 5 === 0 ? "checking retry envelope" : "PASS"}`,
).join("\n")

const phaseFToolEvents: ToolLogEntry[] = [
  {
    id: "tool-queued",
    kind: "status",
    text: "[task] queued: compile SMTP parser with bounded output",
    status: "pending",
    createdAt: now - 9000,
  },
  {
    id: "tool-running",
    kind: "call",
    text: JSON.stringify({
      tool: {
        name: "run_shell",
        args: { command: "gcc -Wall -Wextra smtp_server.c -o smtp_server", cwd: "/tmp/bb_p16_phase_f_smtp" },
      },
    }),
    status: "pending",
    createdAt: now - 8500,
  },
  {
    id: "tool-streaming-output",
    kind: "result",
    text: longToolOutput,
    status: "success",
    createdAt: now - 8000,
    display: {
      title: "run_shell(gcc -Wall -Wextra smtp_server.c)",
      category: "exec",
      summary: ["stdout 34 lines", "cwd /tmp/bb_p16_phase_f_smtp"],
      detail: longToolOutput.split("\n"),
    },
  },
  {
    id: "tool-approval",
    kind: "status",
    text: "[permission] Await approval: apply_patch · workspace-write · cwd /tmp/bb_p16_phase_f_smtp · scope project",
    status: "pending",
    createdAt: now - 7600,
  },
  {
    id: "tool-approved",
    kind: "status",
    text: "[permission] Approved once: apply_patch · request perm-phase-f · scope project",
    status: "success",
    createdAt: now - 7400,
  },
  {
    id: "tool-denied",
    kind: "error",
    text: "Denied once: run_shell rm -rf /tmp/bb_p16_phase_f_smtp · dangerous command blocked · retry with safer command",
    status: "error",
    createdAt: now - 7000,
  },
  {
    id: "tool-failed-retryable",
    kind: "error",
    text: "failed_retryable: tests/smtp_invalid_recipient.sh exited 1 · inspect stderr and retry after patch",
    status: "error",
    createdAt: now - 6600,
  },
  {
    id: "tool-failed-final",
    kind: "error",
    text: "failed_final: smtp_tls_optional.sh still fails after retry budget exhausted",
    status: "error",
    createdAt: now - 6200,
  },
  {
    id: "tool-cancelled",
    kind: "status",
    text: "cancelled: user stopped long-running SMTP soak test after 93s",
    status: "error",
    createdAt: now - 5800,
  },
  {
    id: "tool-stale",
    kind: "status",
    text: "stale_replay_ignored: old tool result ignored after session recovery",
    status: "success",
    createdAt: now - 5400,
  },
  {
    id: "tool-subagent",
    kind: "status",
    text: "[task] tester · subagent provenance · completed focused SMTP regression matrix",
    status: "success",
    createdAt: now - 5000,
  },
]

const dirtyWorkingTreeSummary = {
  kind: "dirty" as const,
  workspace: "/tmp/bb_p16_phase_f_smtp",
  repoRoot: "/tmp/bb_p16_phase_f_smtp",
  changedFiles: [
    { path: "smtp_server.c", status: " M", staged: false, unstaged: true, untracked: false },
    { path: "tests/smtp_invalid_recipient.sh", status: " M", staged: false, unstaged: true, untracked: false },
    { path: "notes/phase_f_review.md", status: "??", staged: false, unstaged: false, untracked: true },
  ],
  additions: 9,
  deletions: 2,
  patch: unifiedDiff,
  untrackedCount: 1,
  warnings: ["1 untracked file listed; untracked file contents are not included in the patch preview."],
}

const tasks: TaskEntry[] = [
  { id: "task-reviewer", sessionId: "sess-p16", description: "Review SMTP parser edge cases", subagentType: "reviewer", status: "completed", kind: "task", outputExcerpt: "Found recipient validation gap and recommended focused guard.", artifactPath: "/tmp/bb_p16_live/task-reviewer.jsonl", updatedAt: now - 2600 },
  { id: "task-impl", sessionId: "sess-p16", description: "Implement SMTP recipient validation", subagentType: "implementer", status: "running", kind: "task", outputExcerpt: "Editing smtp_server.c and adding invalid-recipient test.", artifactPath: "/tmp/bb_p16_live/task-impl.jsonl", updatedAt: now - 1800 },
  { id: "task-tests", sessionId: "sess-p16", description: "Run focused SMTP test matrix", subagentType: "tester", status: "blocked", kind: "task", error: "Waiting for implementer artifact.", updatedAt: now - 1200 },
]

const permissionRequest: PermissionRequest = {
  requestId: "perm-p16-diff",
  tool: "apply_patch",
  kind: "workspace-write",
  rewindable: true,
  summary: "Modify smtp_server.c to reject invalid RCPT recipients and add a focused shell test.",
  diffText: unifiedDiff,
  ruleSuggestion: "allow apply_patch in /tmp/bb_p16_live_smtp only",
  defaultScope: "project",
  effectiveScope: "project",
  cwd: "/tmp/bb_p16_live_smtp",
  reason: "User requested an SMTP recipient validation fix in a dummy workspace.",
  launchPermissionMode: "prompt",
  enginePermissionMode: "approval-required",
  agentId: "agent-impl",
  agentLabel: "implementer agent",
  taskId: "task-impl",
  taskLabel: "Implement SMTP recipient validation",
  createdAt: now - 1000,
}

const permissionSummaryRequest: PermissionRequest = {
  ...permissionRequest,
  requestId: "perm-p16-summary",
  summary: "Run a focused SMTP validation command in the dummy workspace.",
  diffText: null,
}

const commonProps = (overrides: Partial<ReplViewProps> = {}): ReplViewProps => ({
  tuiConfig: {
    ...DEFAULT_RESOLVED_TUI_CONFIG,
    landing: { ...DEFAULT_RESOLVED_TUI_CONFIG.landing, variant: "suppressed" },
    subagents: { ...DEFAULT_RESOLVED_TUI_CONFIG.subagents, enabled: true, stripEnabled: true, taskboardEnabled: true, focusEnabled: true },
  },
  configPath: "/tmp/bb_p16_visual_dummy/breadboard.yaml",
  sessionId: "sess-p16-surface",
  conversation,
  toolEvents,
  rawEvents: [],
  liveSlots: [],
  status: "Ready",
  pendingResponse: false,
  mainFollowTail: true,
  disconnected: false,
  mode: "build",
  permissionMode: "prompt",
  hints: ["ctrl+b tasks", "ctrl+k model", "ctrl+o transcript"],
  stats: baseStats,
  modelMenu: { status: "hidden" },
  skillsMenu: { status: "hidden" },
  inspectMenu: { status: "hidden" },
  guardrailNotice: null,
  activity: { primary: "idle", label: "ready", updatedAt: now, displayedAt: now, seq: 1 },
  runtimeFlags: undefined,
  thinkingArtifact: null,
  thinkingPreview: null,
  viewClearAt: null,
  viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: true, toolRail: true, toolInline: true, showReasoning: true, diffLineNumbers: true },
  todoScopeKey: "workspace:/tmp/bb_p16_visual_dummy",
  todoScopeLabel: "workspace",
  todoScopeStale: false,
  todoScopeOrder: [],
  todoStore,
  todos: [],
  tasks,
  workGraph,
  ctreeSnapshot: null,
  ctreeTree: null,
  ctreeTreeStatus: "idle",
  ctreeTreeError: null,
  ctreeStage: "p16-design",
  ctreeIncludePreviews: false,
  ctreeSource: "visual-qc",
  ctreeUpdatedAt: now,
  permissionRequest: null,
  permissionError: null,
  permissionQueueDepth: 0,
  rewindMenu: { status: "hidden" },
  onSubmit: async () => {},
  onModelMenuOpen: async () => {},
  onModelSelect: async () => {},
  onModelMenuCancel: () => {},
  onSkillsMenuOpen: async () => {},
  onSkillsMenuCancel: () => {},
  onSkillsApply: async () => {},
  onGuardrailToggle: () => {},
  onGuardrailDismiss: () => {},
  onPermissionDecision: async () => {},
  onTaskAction: async () => false,
  onRewindClose: () => {},
  onRewindRestore: async () => {},
  onListFiles: async () => [],
  onReadFile: async (filePath) => ({ path: filePath, content: "", truncated: false, total_bytes: 0 }),
  onListRecentSessions: async () => [],
  onAttachSession: async () => false,
  onCtreeRequest: async () => {},
  onCtreeRefresh: async () => {},
  ...overrides,
})

const renderSurface = async (input: {
  surface: SurfaceId
  label: string
  width: number
  props: ReplViewProps
  interactions?: (stdin: { write: (value: string) => void }) => Promise<void>
  expectedMarkers: readonly string[]
  interactionPath: string
}): Promise<SurfaceCase> => {
  const stdout = process.stdout as NodeJS.WriteStream & { columns?: number; rows?: number }
  const previousColumns = stdout.columns
  const previousRows = stdout.rows
  stdout.columns = input.width
  stdout.rows = 38
  try {
    const { lastFrame, unmount, stdin } = render(<ReplView {...input.props} />, { width: input.width })
    await flush(30)
    if (input.interactions) {
      await input.interactions(stdin)
      await flush(80)
    }
    const frame = stripAnsi(lastFrame() ?? "")
    unmount()
    return {
      surface: input.surface,
      label: input.label,
      width: input.width,
      frame,
      artifactKind: "production_repl_controller",
      interactionPath: input.interactionPath,
      expectedMarkers: input.expectedMarkers,
    }
  } finally {
    stdout.columns = previousColumns
    stdout.rows = previousRows
  }
}

const renderAllCases = async (): Promise<SurfaceCase[]> => {
  const widths = [72, 100, 140]
  const cases: SurfaceCase[] = []
  for (const width of widths) {
    cases.push(
      await renderSurface({
        surface: "tool_long_output",
        label: `tool long-output collapse ${width} cols`,
        width,
        props: commonProps({ conversation: conversation.slice(0, 1), toolEvents: phaseFToolEvents.slice(0, 3) }),
        expectedMarkers: ["run_shell", "stdout 34 lines", "cwd /tmp/bb_p16_phase_f_smtp", "hidden", "[ready] enter send"],
        interactionPath: "ReplView production render with long tool output and truncation metadata",
      }),
    )
    cases.push(
      await renderSurface({
        surface: "tool_final_states",
        label: `tool final states ${width} cols`,
        width,
        props: commonProps({ conversation: conversation.slice(0, 1), toolEvents: phaseFToolEvents }),
        expectedMarkers: ["Await approval", "Approved once", "Denied once", "failed_retryable", "failed_final", "cancelled", "stale_replay_ignored", "subagent provenance"],
        interactionPath: "ReplView production render with seeded tool final states",
      }),
    )
    cases.push(
      await renderSurface({
        surface: "permission_summary",
        label: `permission summary ${width} cols`,
        width,
        props: commonProps({ conversation: [], toolEvents: [], permissionRequest: permissionSummaryRequest, permissionQueueDepth: 1 }),
        expectedMarkers: ["Permission required", "apply_patch", "workspace-write", "scope project", "Cwd:", "Agent:", "Task:", "No diff attached", "allow", "deny"],
        interactionPath: "ReplView production render with active no-diff permission request summary",
      }),
    )
    cases.push(
      await renderSurface({
        surface: "permission_diff",
        label: `permission diff ${width} cols`,
        width,
        props: commonProps({ conversation: [], toolEvents: [], permissionRequest, permissionQueueDepth: 1 }),
        expectedMarkers: ["Permission required", "Diff", "smtp_server.c", "is_valid_mailbox", "allow", "deny"],
        interactionPath: "ReplView production render with attached diff permission request; diff tab opens by default",
      }),
    )
    cases.push(
      await renderSurface({
        surface: "permission_rules",
        label: `permission rules ${width} cols`,
        width,
        props: commonProps({ conversation: [], toolEvents: [], permissionRequest, permissionQueueDepth: 1 }),
        interactions: async (stdin) => {
          stdin.write("\t")
        },
        expectedMarkers: ["Permission required", "Rules", "Default scope", "Suggested rule", "sends allow-always", "Stored policy is", "deny"],
        interactionPath: "ReplView production render with attached diff permission request; Tab to rules panel",
      }),
    )
    cases.push(
      await renderSurface({
        surface: "diff_command",
        label: `/diff command result ${width} cols`,
        width,
        props: commonProps({
          conversation: [],
          toolEvents: [],
          onReadWorkingTreeDiff: async () => dirtyWorkingTreeSummary,
        }),
        interactions: async (stdin) => {
          stdin.write("/diff\r")
        },
        expectedMarkers: ["Working-tree diff", "Dirty state", "smtp_server.c", "Unified diff preview", "Navigation", "read-only"],
        interactionPath: "ReplView production command path: type /diff and render local working-tree result",
      }),
    )
  }
  return cases
}

const reportCase = (entry: SurfaceCase): SurfaceReport => {
  const lines = entry.frame.split(/\r?\n/)
  const maxVisibleWidth = Math.max(0, ...lines.map((line) => visibleWidth(stripAnsiCodes(line))))
  const missingMarkers = entry.expectedMarkers.filter((marker) => !entry.frame.includes(marker))
  const forbidden = ["undefined", "[object Object]", "approve and apply", "policy changed", "persisted policy"]
  const forbiddenHits = forbidden.filter((marker) => entry.frame.includes(marker))
  return {
    surface: entry.surface,
    label: entry.label,
    width: entry.width,
    artifactKind: entry.artifactKind,
    interactionPath: entry.interactionPath,
    lineCount: lines.length,
    maxVisibleWidth,
    markersPass: missingMarkers.length === 0,
    widthPass: maxVisibleWidth <= entry.width,
    forbiddenPass: forbiddenHits.length === 0,
    expectedMarkers: entry.expectedMarkers,
    missingMarkers,
    forbiddenHits,
    frame: entry.frame,
  }
}

const buildKnownBadCalibration = (): string => {
  const detectors = [
    {
      id: "approval-overclaim-apply",
      badFrame: "Review/approval actions: approve and apply patch now",
      expectedHit: "approve and apply",
    },
    {
      id: "permission-policy-invention",
      badFrame: "Permission status: policy changed and persisted policy updated",
      expectedHit: "policy changed",
    },
    {
      id: "unrendered-object",
      badFrame: "Permission required [object Object]",
      expectedHit: "[object Object]",
    },
  ]
  const rows = detectors.map((item) => {
    const observed = item.badFrame.includes(item.expectedHit)
    return `| ${item.id} | ${item.expectedHit} | ${observed ? "observed_red" : "missing"} |`
  })
  return [
    "# Phase F Approval/Diff Known-Bad Calibration",
    "",
    `Generated: ${new Date().toISOString()}`,
    "",
    "These are detector calibrations for the Phase F visual/contact-sheet gate. They prove the gate can reject misleading approval/diff/permission copy; they are not live product reproductions.",
    "",
    "| Detector | Expected Bad Signature | Result |",
    "| --- | --- | --- |",
    ...rows,
    "",
    "Acceptance rule: all expected bad signatures must be observable by the same forbidden-marker detector used by the Phase F visual evidence report.",
    "",
  ].join("\n")
}

const buildMarkdown = (surface: SurfaceId, reports: readonly SurfaceReport[]): string => {
  const parts = [`# P16 ${surface.replaceAll("_", " ")} Contact Sheet`, "", `Generated: ${new Date().toISOString()}`, "Artifact kind: production ReplView/controller render; not real-host proof.", ""]
  for (const report of reports) {
    parts.push(
      `## ${report.label}`,
      "",
      `- width: ${report.width}`,
      `- artifact kind: ${report.artifactKind}`,
      `- interaction path: ${report.interactionPath}`,
      `- max visible width: ${report.maxVisibleWidth}`,
      `- markers pass: ${report.markersPass}`,
      `- width pass: ${report.widthPass}`,
      `- forbidden pass: ${report.forbiddenPass}`,
      "",
      "```text",
      report.frame,
      "```",
      "",
    )
  }
  return `${parts.join("\n")}\n`
}

const buildSvg = (surface: SurfaceId, reports: readonly SurfaceReport[]): string => {
  const lineHeight = 18
  const panelGap = 28
  const panelPad = 18
  const panelWidth = 1260
  const maxLinesPerPanel = 96
  const renderedReports = reports.map((report) => ({ ...report, lines: report.frame.split(/\r?\n/).slice(0, maxLinesPerPanel) }))
  const panelHeights = renderedReports.map((report) => panelPad * 2 + lineHeight * (report.lines.length + 3))
  const headerHeight = 58
  const width = panelWidth + panelPad * 2
  const height = panelHeights.reduce((sum, value) => sum + value, 0) + panelGap * Math.max(0, reports.length - 1) + panelPad * 2 + headerHeight
  let y = panelPad + headerHeight
  const panels: string[] = []
  for (let index = 0; index < renderedReports.length; index += 1) {
    const report = renderedReports[index]!
    const ok = report.markersPass && report.widthPass && report.forbiddenPass
    const panelHeight = panelHeights[index] ?? 180
    panels.push(`<rect x="${panelPad}" y="${y}" width="${panelWidth}" height="${panelHeight}" rx="14" fill="#14120f" stroke="${ok ? "#f97316" : "#ff4d6d"}" stroke-width="2"/>`)
    panels.push(`<text font-family="DejaVu Sans Mono" x="${panelPad + 18}" y="${y + 30}" fill="#ffcf99" font-size="16" font-weight="700">${escapeXml(report.label)} · width ${report.width} · ${ok ? "PASS" : "FAIL"}</text>`)
    let lineY = y + 58
    for (const line of report.lines) {
      panels.push(`<text font-family="DejaVu Sans Mono" x="${panelPad + 18}" y="${lineY}" fill="#f4efe6" font-size="14">${escapeXml(line)}</text>`)
      lineY += lineHeight
    }
    if (!ok) {
      const failure = [
        report.missingMarkers.length > 0 ? `missing: ${report.missingMarkers.join(", ")}` : null,
        !report.widthPass ? `width ${report.maxVisibleWidth} > ${report.width}` : null,
        report.forbiddenHits.length > 0 ? `forbidden: ${report.forbiddenHits.join(", ")}` : null,
      ].filter(Boolean).join("; ")
      panels.push(`<text font-family="DejaVu Sans Mono" x="${panelPad + 18}" y="${lineY + 8}" fill="#ff4d6d" font-size="13">${escapeXml(failure)}</text>`)
    }
    y += panelHeight + panelGap
  }
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<rect width="100%" height="100%" fill="#0b0a08"/>`,
    `<text font-family="DejaVu Sans" x="${panelPad}" y="${panelPad + 10}" fill="#f8b36a" font-size="18" font-weight="800">BreadBoard P16 ${escapeXml(surface.replaceAll("_", " "))} Contact Sheet</text>`,
    `<text font-family="DejaVu Sans" x="${panelPad}" y="${panelPad + 34}" fill="#9ca3af" font-size="13">Production ReplView/controller render; classified as design evidence, not real-host proof.</text>`,
    ...panels,
    "</svg>",
  ].join("\n")
}

const main = async () => {
  const runId = `phase_f_visual_${timestamp()}`
  const outDir = path.join(PHASE_F_DIR, runId)
  await fs.mkdir(outDir, { recursive: true })

  const [gitHead, gitStatus, packageJson] = await Promise.all([
    run("git", ["rev-parse", "HEAD"]),
    run("git", ["status", "--short"]),
    fs.readFile(path.join(ROOT_DIR, "package.json"), "utf8"),
  ])
  const packageScripts = JSON.parse(packageJson).scripts ?? {}
  const cases = await renderAllCases()
  const reports = cases.map(reportCase)
  const bySurface = new Map<SurfaceId, SurfaceReport[]>()
  for (const report of reports) {
    bySurface.set(report.surface, [...(bySurface.get(report.surface) ?? []), report])
  }

  const failures = reports.flatMap((report) => {
    const current: string[] = []
    if (!report.markersPass) current.push(`${report.surface}: missing markers ${report.missingMarkers.join(", ")}`)
    if (!report.widthPass) current.push(`${report.surface}: max width ${report.maxVisibleWidth} exceeds ${report.width}`)
    if (!report.forbiddenPass) current.push(`${report.surface}: forbidden text ${report.forbiddenHits.join(", ")}`)
    return current
  })

  const artifactPaths: Record<string, Record<string, string | null>> = {}
  for (const [surface, surfaceReports] of bySurface.entries()) {
    const md = buildMarkdown(surface, surfaceReports)
    const svg = buildSvg(surface, surfaceReports)
    const mdPath = path.join(outDir, `${surface}_contact_sheet.md`)
    const svgPath = path.join(outDir, `${surface}_contact_sheet.svg`)
    const pngPath = path.join(outDir, `${surface}_contact_sheet.png`)
    await fs.writeFile(mdPath, md, "utf8")
    await fs.writeFile(svgPath, svg, "utf8")
    const pngCreated = await runConvert(svgPath, pngPath)
    if (!pngCreated) failures.push(`${surface}: ImageMagick convert failed`)
    artifactPaths[surface] = { mdPath, svgPath, pngPath: pngCreated ? pngPath : null }
    if (pngCreated) {
      await fs.copyFile(pngPath, path.join(PHASE_F_DIR, `${surface}_contact_sheet.png`))
      if (surface === "diff_command") {
        await fs.copyFile(pngPath, path.join(PHASE_F_DIR, "diff_contact_sheet.png"))
      }
      if (surface === "permission_summary") {
        await fs.copyFile(pngPath, path.join(PHASE_F_DIR, "permission_contact_sheet.png"))
      }
      if (surface === "tool_long_output") {
        await fs.copyFile(pngPath, path.join(PHASE_F_DIR, "tool_contact_sheet.png"))
      }
    }
    await fs.copyFile(mdPath, path.join(PHASE_F_DIR, `${surface}_contact_sheet.md`))
    await fs.copyFile(svgPath, path.join(PHASE_F_DIR, `${surface}_contact_sheet.svg`))
  }
  const knownBadCalibration = buildKnownBadCalibration()
  await fs.writeFile(path.join(outDir, "approval_known_bad_calibration.md"), knownBadCalibration, "utf8")
  await fs.writeFile(path.join(PHASE_F_DIR, "approval_known_bad_calibration.md"), knownBadCalibration, "utf8")

  const invariantReport = {
    generatedAt: new Date().toISOString(),
    runId,
    source: "scripts/p16_phase_f_visual_evidence.tsx",
    artifactKind: "production_repl_controller",
    verdict: failures.length === 0 ? "pass" : "fail",
    failures,
    cases: reports.map(({ frame, ...rest }) => rest),
    artifactPaths,
  }
  const manifest = {
    generatedAt: new Date().toISOString(),
    runId,
    source: "scripts/p16_phase_f_visual_evidence.tsx",
    gitHead: gitHead.stdout.trim(),
    gitStatusShort: gitStatus.stdout.split(/\r?\n/).filter(Boolean),
    packageScript: packageScripts["p16:phase-f:visual-evidence"] ?? null,
    artifactKind: "production_repl_controller",
    claimBoundary: "Phase F production ReplView/controller visual evidence for tool, permission, and diff surfaces; not real-host proof.",
    surfaces: [...bySurface.keys()],
  }
  await fs.writeFile(path.join(outDir, "phase_f_visual_invariant_report.json"), `${JSON.stringify(invariantReport, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(PHASE_F_DIR, "phase_f_visual_invariant_report.json"), `${JSON.stringify(invariantReport, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(PHASE_F_DIR, "phase_f_visual_contact_sheet_manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify({ outDir, verdict: invariantReport.verdict, failures, artifactPaths }, null, 2)}\n`)
  if (failures.length > 0) process.exitCode = 1
}

await main()
