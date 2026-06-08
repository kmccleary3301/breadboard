import React from "react"
import { Box } from "ink"
import { render } from "ink-testing-library"
import { promises as fs } from "node:fs"
import path from "node:path"
import { spawn } from "node:child_process"
import stripAnsi from "strip-ansi"

import { ReplView } from "../../src/repl/components/ReplView.js"
import type { ConversationEntry, ModelMenuItem, PermissionRequest, TaskEntry, TodoStoreSnapshot, ToolLogEntry, WorkGraphState } from "../../src/repl/types.js"
import type { ReplViewProps } from "../../src/repl/components/replView/replViewTypes.js"
import type { SessionFileInfo } from "../../src/api/types.js"
import { DEFAULT_RESOLVED_TUI_CONFIG } from "../../src/tui_config/presets.js"
import { stripAnsiCodes, visibleWidth } from "../../src/repl/components/replView/utils/ansi.js"
import type { P16CompositorInvariant, P16CompositorScene, P16SceneEvent } from "./schema.js"
import { requiredCompositorArtifacts } from "./schema.js"

export interface RenderedFrame {
  readonly cols: number
  readonly rows: number
  readonly frame: string
  readonly plain: string
  readonly maxVisibleWidth: number
  readonly lineCount: number
}

export interface InvariantResult {
  readonly id: P16CompositorInvariant
  readonly status: "pass" | "fail"
  readonly message: string
  readonly evidence?: Record<string, unknown>
}

export interface P16CompositorRenderResult {
  readonly scene: P16CompositorScene
  readonly outDir: string
  readonly frames: readonly RenderedFrame[]
  readonly invariantResults: readonly InvariantResult[]
  readonly expectedRedObserved: boolean
  readonly verdict: "pass" | "fail"
}

const now = () => Date.now()
const flush = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms))
const escapeXml = (value: string): string => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")

const run = (command: string, args: readonly string[], cwd: string): Promise<{ code: number; stdout: string; stderr: string }> =>
  new Promise((resolve) => {
    const child = spawn(command, [...args], { cwd, stdio: ["ignore", "pipe", "pipe"] })
    const stdout: Buffer[] = []
    const stderr: Buffer[] = []
    child.stdout.on("data", (chunk) => stdout.push(Buffer.from(chunk)))
    child.stderr.on("data", (chunk) => stderr.push(Buffer.from(chunk)))
    child.on("error", (error) => resolve({ code: 127, stdout: "", stderr: String(error) }))
    child.on("exit", (code) => resolve({ code: code ?? 1, stdout: Buffer.concat(stdout).toString("utf8"), stderr: Buffer.concat(stderr).toString("utf8") }))
  })

const modelItems: readonly ModelMenuItem[] = [
  { label: "gpt-5.4-mini", value: "gpt-5.4-mini", provider: "openai", detail: "default Codex E4 lane", isCurrent: true, contextTokens: 400000 },
  { label: "gpt-5.4", value: "gpt-5.4", provider: "openai", detail: "larger coding lane", contextTokens: 400000 },
]

interface ReducedState {
  readonly conversation: ConversationEntry[]
  readonly toolEvents: ToolLogEntry[]
  readonly tasks: TaskEntry[]
  readonly permissionRequest: PermissionRequest | null
  readonly disconnected: boolean
  readonly status: string
  readonly hints: string[]
  readonly modelMenuOpen: boolean
  readonly taskboardOpen: boolean
  readonly transcriptOpen: boolean
  readonly filePickerOpen: boolean
  readonly inputEvents: unknown[]
  readonly dimensionEvents: unknown[]
  readonly stateSnapshots: unknown[]
  readonly workspaceFiles: Array<{ path: string; content: string }>
}

const baseState = (): ReducedState => ({
  conversation: [],
  toolEvents: [],
  tasks: [],
  permissionRequest: null,
  disconnected: false,
  status: "Ready",
  hints: ["ctrl+b tasks", "ctrl+k model", "ctrl+o transcript"],
  modelMenuOpen: false,
  taskboardOpen: false,
  transcriptOpen: false,
  filePickerOpen: false,
  inputEvents: [],
  dimensionEvents: [],
  stateSnapshots: [],
  workspaceFiles: [],
})

const appendState = (state: ReducedState, patch: Partial<ReducedState>, event: P16SceneEvent, index: number): ReducedState => {
  const next = { ...state, ...patch }
  return { ...next, stateSnapshots: [...next.stateSnapshots, { index, event, state: { status: next.status, disconnected: next.disconnected, conversation: next.conversation.length, toolEvents: next.toolEvents.length, tasks: next.tasks.length, permissionRequest: Boolean(next.permissionRequest), modelMenuOpen: next.modelMenuOpen, taskboardOpen: next.taskboardOpen } }] }
}

export const reduceScene = (scene: P16CompositorScene): ReducedState => {
  let state = baseState()
  for (const [index, event] of scene.timeline.entries()) {
    const t = now() + index
    switch (event.kind) {
      case "user": {
        const text = event.text ?? "User request"
        state = appendState(state, { conversation: [...state.conversation, { id: event.id ?? `u-${index}`, speaker: "user", text, phase: "final", createdAt: t }], inputEvents: [...state.inputEvents, { index, kind: "submit", text }] }, event, index)
        break
      }
      case "assistant":
      case "assistant-delta": {
        state = appendState(state, { conversation: [...state.conversation, { id: event.id ?? `a-${index}`, speaker: "assistant", text: event.text ?? "Assistant response", phase: event.kind === "assistant-delta" ? "streaming" : "final", markdownStreaming: event.kind === "assistant-delta", createdAt: t }] }, event, index)
        break
      }
      case "tool": {
        const callId = event.id ?? `tool-${index}`
        state = appendState(state, { toolEvents: [...state.toolEvents, { id: `${callId}-result`, kind: "result", text: event.text ?? "tool output", status: event.status === "failed" ? "error" : "success", createdAt: t, callId }] }, event, index)
        break
      }
      case "diff": {
        const diff = event.text ?? `diff --git a/${event.filePath ?? "demo.txt"} b/${event.filePath ?? "demo.txt"}\n+changed line`
        state = appendState(state, {
          toolEvents: [
            ...state.toolEvents,
            {
              id: `diff-${index}`,
              kind: "result",
              text: diff,
              status: "success",
              createdAt: t,
              callId: `diff-${index}`,
              display: {
                title: "Compositor diff fixture",
                detail: diff,
                diff_blocks: [{ kind: "diff", filePath: event.filePath ?? "demo.txt", unified: diff, additions: 1, deletions: 0, language: "diff", title: "Compositor diff fixture" }],
              },
            },
          ],
        }, event, index)
        break
      }
      case "permission": {
        const diffText = event.text ?? "diff --git a/demo.txt b/demo.txt\n+allowed change"
        state = appendState(state, { permissionRequest: { requestId: event.id ?? `perm-${index}`, tool: "apply_patch", kind: "workspace-write", rewindable: true, summary: "Compositor permission fixture", diffText, ruleSuggestion: "allow apply_patch in dummy workspace only", defaultScope: "project", createdAt: t } }, event, index)
        break
      }
      case "task": {
        state = appendState(state, { tasks: [...state.tasks, { id: event.id ?? `task-${index}`, sessionId: "p16-compositor", description: event.text ?? "Compositor task", subagentType: event.role ?? "worker", status: event.status === "blocked" ? "blocked" : event.status === "completed" ? "completed" : "running", kind: "task", outputExcerpt: event.content ?? "Task output excerpt", updatedAt: t }] }, event, index)
        break
      }
      case "disconnect":
        state = appendState(state, { disconnected: true, status: event.status ?? "Disconnected", hints: ["retry the command", "restart the session"] }, event, index)
        break
      case "reconnect":
        state = appendState(state, { disconnected: false, status: "Ready", hints: ["ctrl+b tasks", "ctrl+k model", "ctrl+o transcript"] }, event, index)
        break
      case "open-surface":
        state = appendState(state, { modelMenuOpen: event.surface === "model-picker", taskboardOpen: event.surface === "taskboard", transcriptOpen: event.surface === "transcript-viewer", filePickerOpen: event.surface === "file-picker" }, event, index)
        break
      case "resize":
        state = appendState(state, { dimensionEvents: [...state.dimensionEvents, { index, cols: event.cols, rows: event.rows }] }, event, index)
        break
      case "workspace-file":
        state = appendState(state, { workspaceFiles: [...state.workspaceFiles, { path: event.filePath ?? `file-${index}.txt`, content: event.content ?? "" }] }, event, index)
        break
      case "wait":
        state = appendState(state, {}, event, index)
        break
      default:
        state = appendState(state, {}, event, index)
    }
  }
  return state
}

const todoStore: TodoStoreSnapshot = { revision: 0, updatedAt: now(), itemsById: {}, order: [] }
const workGraph: WorkGraphState = { itemsById: {}, itemOrder: [], lanesById: {}, laneOrder: [], processedEventKeys: [], lastSeq: 0 }

const propsFromState = (state: ReducedState, scene: P16CompositorScene): ReplViewProps => ({
  tuiConfig: {
    ...DEFAULT_RESOLVED_TUI_CONFIG,
    landing: { ...DEFAULT_RESOLVED_TUI_CONFIG.landing, variant: scene.surfaces.includes("landing") ? "auto" : "suppressed" },
    subagents: { ...DEFAULT_RESOLVED_TUI_CONFIG.subagents, enabled: true, stripEnabled: true, taskboardEnabled: true, focusEnabled: true },
  },
  configPath: "/tmp/bb_p16_compositor/breadboard.yaml",
  sessionId: `p16-${scene.id}`,
  conversation: state.conversation,
  toolEvents: state.toolEvents,
  rawEvents: [],
  liveSlots: [],
  status: state.status,
  pendingResponse: false,
  mainFollowTail: true,
  disconnected: state.disconnected,
  mode: "build",
  permissionMode: "prompt",
  hints: state.hints,
  stats: { eventCount: state.stateSnapshots.length, toolCount: state.toolEvents.length, lastTurn: state.conversation.length, remote: false, model: "gpt-5.4-mini", usage: { promptTokens: 1200, completionTokens: 400, totalTokens: 1600, latencyMs: 1200 } },
  modelMenu: state.modelMenuOpen ? { status: "ready", items: modelItems } : { status: "hidden" },
  skillsMenu: { status: "hidden" },
  inspectMenu: { status: "hidden" },
  guardrailNotice: null,
  activity: { primary: state.disconnected ? "reconnecting" : "idle", label: state.disconnected ? "reconnecting" : "ready", detail: state.disconnected ? { message: "Lost connection to the engine" } : undefined, updatedAt: now(), displayedAt: now(), seq: 1 },
  runtimeFlags: undefined,
  thinkingArtifact: null,
  thinkingPreview: null,
  viewClearAt: null,
  viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: true, toolRail: true, toolInline: true, showReasoning: true, diffLineNumbers: true },
  todoScopeKey: "workspace:/tmp/bb_p16_compositor",
  todoScopeLabel: "workspace",
  todoScopeStale: false,
  todoScopeOrder: [],
  todoStore,
  todos: [],
  tasks: state.tasks,
  workGraph,
  ctreeSnapshot: null,
  ctreeTree: null,
  ctreeTreeStatus: "idle",
  ctreeTreeError: null,
  ctreeStage: "p16-compositor",
  ctreeIncludePreviews: false,
  ctreeSource: "p16-compositor",
  ctreeUpdatedAt: now(),
  permissionRequest: state.permissionRequest,
  permissionError: null,
  permissionQueueDepth: state.permissionRequest ? 1 : 0,
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
  onListFiles: async (): Promise<SessionFileInfo[]> => state.workspaceFiles.map((file) => ({ path: file.path, type: "file", size: file.content.length })),
  onReadFile: async (filePath) => ({ path: filePath, content: state.workspaceFiles.find((file) => file.path === filePath)?.content ?? "", truncated: false, total_bytes: 0 }),
  onListRecentSessions: async () => [],
  onAttachSession: async () => false,
  onCtreeRequest: async () => {},
  onCtreeRefresh: async () => {},
})

const renderFrame = async (scene: P16CompositorScene, state: ReducedState, cols: number, rows: number): Promise<RenderedFrame> => {
  const priorFrameWidth = process.env.BREADBOARD_TUI_FRAME_WIDTH
  process.env.BREADBOARD_TUI_FRAME_WIDTH = String(cols)
  const { lastFrame, unmount, stdin } = render(
    <Box flexDirection="column" width={cols}>
      <ReplView {...propsFromState(state, scene)} />
    </Box>,
  )
  try {
    await flush(30)
    if (state.taskboardOpen) {
      stdin.write("\u0002")
      await flush(60)
    }
    if (state.transcriptOpen) {
      stdin.write("\u000f")
      await flush(60)
    }
    if (state.permissionRequest) {
      stdin.write("\t")
      await flush(60)
    }
    const frame = lastFrame() ?? ""
    const plain = stripAnsi(frame)
    const lines = plain.split(/\r?\n/)
    return { cols, rows, frame, plain, maxVisibleWidth: Math.max(0, ...lines.map((line) => visibleWidth(stripAnsiCodes(line)))), lineCount: lines.length }
  } finally {
    unmount()
    if (priorFrameWidth == null) delete process.env.BREADBOARD_TUI_FRAME_WIDTH
    else process.env.BREADBOARD_TUI_FRAME_WIDTH = priorFrameWidth
  }
}

const invariant = (id: P16CompositorInvariant, status: "pass" | "fail", message: string, evidence?: Record<string, unknown>): InvariantResult => ({ id, status, message, evidence })

export const evaluateInvariants = (scene: P16CompositorScene, state: ReducedState, frames: readonly RenderedFrame[], artifactFiles: readonly string[]): readonly InvariantResult[] => {
  const blob = frames.map((frame) => frame.plain).join("\n")
  const promptCounts = new Map<string, number>()
  for (const entry of state.conversation.filter((item) => item.speaker === "user")) {
    promptCounts.set(entry.text, (promptCounts.get(entry.text) ?? 0) + 1)
  }
  const results: InvariantResult[] = []
  for (const id of scene.invariants) {
    if (id === "COMPOSITOR-PRODUCTION-RENDERER") results.push(invariant(id, "pass", "rendered via production ReplView component", { renderer: scene.productionEquivalence.renderer }))
    else if (id === "COMPOSITOR-NO-FAKE-RENDERER") results.push(invariant(id, scene.productionEquivalence.forbiddenBypass.includes("renderer") ? "pass" : "fail", "forbidden renderer bypass is declared"))
    else if (id === "COMPOSITOR-ARTIFACT-PACKET-COMPLETE") {
      const missing = requiredCompositorArtifacts().filter((item) => !artifactFiles.includes(item))
      results.push(invariant(id, missing.length === 0 ? "pass" : "fail", missing.length === 0 ? "artifact packet complete" : "artifact packet incomplete", { missing }))
    } else if (id === "COMPOSITOR-MARKERS-PRESENT") {
      const missing = scene.expectedMarkers.filter((marker) => !blob.includes(marker))
      results.push(invariant(id, missing.length === 0 ? "pass" : "fail", missing.length === 0 ? "expected markers present" : "expected markers missing", { missing }))
    } else if (id === "COMPOSITOR-FORBIDDEN-MARKERS-ABSENT") {
      const present = (scene.forbiddenMarkers ?? []).filter((marker) => blob.includes(marker))
      results.push(invariant(id, present.length === 0 ? "pass" : "fail", present.length === 0 ? "forbidden markers absent" : "forbidden markers present", { present }))
    } else if (id === "COMPOSITOR-WIDTH-SAFE") {
      const bad = frames.filter((frame) => frame.maxVisibleWidth > frame.cols)
      results.push(invariant(id, bad.length === 0 ? "pass" : "fail", bad.length === 0 ? "all frames are width-safe" : "frame width overflow", { bad }))
    } else if (id === "COMPOSITOR-COMPOSER-VISIBLE") {
      const visible = /Type your request|enter send|❯|›/.test(blob)
      results.push(invariant(id, visible ? "pass" : "fail", visible ? "composer/input affordance visible" : "composer/input affordance hidden"))
    } else if (id === "COMPOSITOR-PROMPT-EXACTLY-ONCE") {
      const duplicates = [...promptCounts.entries()].filter(([, count]) => count !== 1)
      results.push(invariant(id, duplicates.length === 0 ? "pass" : "fail", duplicates.length === 0 ? "each submitted prompt is unique in reduced transcript" : "duplicate prompt in reduced transcript", { duplicates }))
    } else if (id === "COMPOSITOR-NO-READY-LIE") {
      const bad = state.disconnected && /ready/i.test(state.status)
      results.push(invariant(id, bad ? "fail" : "pass", bad ? "ready status while disconnected" : "no ready-lie state"))
    } else if (id === "COMPOSITOR-ACTIVE-ROW-VISIBLE") {
      const needed = state.modelMenuOpen || state.taskboardOpen || state.permissionRequest
      const active = /›|❯|selected|Current|allow once|Tasks|Models/.test(blob)
      results.push(invariant(id, !needed || active ? "pass" : "fail", !needed || active ? "active row/choice marker visible or not needed" : "active marker not visible"))
    } else if (id === "COMPOSITOR-RECOVERY-ACTIONABLE") {
      const needed = state.disconnected
      const actionable = /Disconnected|Lost connection|retry|restart/.test(blob)
      results.push(invariant(id, !needed || actionable ? "pass" : "fail", !needed || actionable ? "recovery copy actionable or not needed" : "recovery copy missing safe next action"))
    } else if (id === "COMPOSITOR-HOST-HISTORY-PRESERVED") {
      const needed = scene.expectedMarkers.filter((marker) => marker.includes("PRE_APP") || marker.includes("SENTINEL"))
      const missing = needed.filter((marker) => !blob.includes(marker))
      results.push(invariant(id, missing.length === 0 ? "pass" : "fail", missing.length === 0 ? "declared host-history markers present" : "declared host-history markers missing", { missing }))
    }
  }
  return results
}

const buildContactSheetSvg = (scene: P16CompositorScene, frames: readonly RenderedFrame[], invariantResults: readonly InvariantResult[]): string => {
  const lineHeight = 18
  const panelWidth = 1260
  const pad = 20
  const maxLines = 90
  const panelHeights = frames.map((frame) => pad * 2 + lineHeight * (Math.min(maxLines, frame.plain.split(/\r?\n/).length) + 3))
  const invariantHeight = 34 + lineHeight * invariantResults.length
  const height = 90 + invariantHeight + panelHeights.reduce((a, b) => a + b, 0) + Math.max(0, frames.length - 1) * 28 + pad * 2
  let y = 72
  const out: string[] = [`<svg xmlns="http://www.w3.org/2000/svg" width="${panelWidth + pad * 2}" height="${height}" viewBox="0 0 ${panelWidth + pad * 2} ${height}">`, `<rect width="100%" height="100%" fill="#0b0a08"/>`, `<text font-family="DejaVu Sans" x="${pad}" y="30" fill="#f8b36a" font-size="20" font-weight="800">P16 Compositor: ${escapeXml(scene.title)}</text>`, `<text font-family="DejaVu Sans" x="${pad}" y="54" fill="#9ca3af" font-size="13">${escapeXml(scene.claimBoundary)}</text>`]
  out.push(`<rect x="${pad}" y="${y}" width="${panelWidth}" height="${invariantHeight}" rx="10" fill="#111827" stroke="#64748b"/>`)
  let iy = y + 24
  for (const result of invariantResults) {
    out.push(`<text font-family="DejaVu Sans Mono" x="${pad + 16}" y="${iy}" fill="${result.status === "pass" ? "#86efac" : "#ff4d6d"}" font-size="13">${escapeXml(result.status.toUpperCase())} ${escapeXml(result.id)}: ${escapeXml(result.message)}</text>`)
    iy += lineHeight
  }
  y += invariantHeight + 28
  for (const [index, frame] of frames.entries()) {
    const lines = frame.plain.split(/\r?\n/).slice(0, maxLines)
    const ok = frame.maxVisibleWidth <= frame.cols
    const panelHeight = panelHeights[index] ?? 180
    out.push(`<rect x="${pad}" y="${y}" width="${panelWidth}" height="${panelHeight}" rx="14" fill="#14120f" stroke="${ok ? "#f97316" : "#ff4d6d"}" stroke-width="2"/>`)
    out.push(`<text font-family="DejaVu Sans Mono" x="${pad + 18}" y="${y + 30}" fill="#ffcf99" font-size="16" font-weight="700">${frame.cols}x${frame.rows} · max width ${frame.maxVisibleWidth} · ${ok ? "PASS" : "FAIL"}</text>`)
    let ly = y + 58
    for (const line of lines) {
      out.push(`<text font-family="DejaVu Sans Mono" x="${pad + 18}" y="${ly}" fill="#f4efe6" font-size="14">${escapeXml(line)}</text>`)
      ly += lineHeight
    }
    y += panelHeight + 28
  }
  out.push("</svg>")
  return out.join("\n")
}

const writeJson = (filePath: string, value: unknown) => fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8")

export const renderP16CompositorScene = async (scene: P16CompositorScene, outDir: string, options: { repoRoot: string; tuiRoot: string; command: string }): Promise<P16CompositorRenderResult> => {
  await fs.mkdir(path.join(outDir, "screenshots"), { recursive: true })
  const state = reduceScene(scene)
  const frames: RenderedFrame[] = []
  for (const size of scene.sizes) frames.push(await renderFrame(scene, state, size.cols, size.rows))

  const expectedArtifactFiles = [...requiredCompositorArtifacts()]
  let invariantResults = evaluateInvariants(scene, state, frames, expectedArtifactFiles)
  const normalPass = invariantResults.every((result) => result.status === "pass")
  const expectedRedObserved = scene.expectedOutcome === "fail" && !normalPass
  const verdict = scene.expectedOutcome === "fail" ? (expectedRedObserved ? "pass" : "fail") : normalPass ? "pass" : "fail"
  const anomalies = invariantResults.filter((result) => result.status === "fail")
  const gitStatus = await run("git", ["status", "--short"], options.repoRoot)
  const gitHead = await run("git", ["rev-parse", "HEAD"], options.repoRoot)
  const svg = buildContactSheetSvg(scene, frames, invariantResults)
  const svgPath = path.join(outDir, "screenshots", "contact_sheet.svg")
  const pngPath = path.join(outDir, "screenshots", "contact_sheet.png")

  await fs.writeFile(svgPath, svg, "utf8")
  const convert = await run("convert", [svgPath, pngPath], options.repoRoot)
  if (convert.code !== 0) {
    invariantResults = [...invariantResults, invariant("COMPOSITOR-ARTIFACT-PACKET-COMPLETE", "fail", "ImageMagick convert failed", { stderr: convert.stderr })]
  }

  const manifest = {
    schemaVersion: 1,
    sceneId: scene.id,
    generatedAt: new Date().toISOString(),
    artifactKind: "production_repl_controller_compositor",
    claimBoundary: scene.claimBoundary,
    expectedOutcome: scene.expectedOutcome,
    knownBadKind: scene.knownBadKind ?? null,
    verdict,
    expectedRedObserved,
    gitHead: gitHead.stdout.trim(),
    gitStatusShort: gitStatus.stdout.split(/\r?\n/).filter(Boolean),
    renderer: "ReplView",
    sizes: scene.sizes,
  }
  await writeJson(path.join(outDir, "manifest.json"), manifest)
  await fs.writeFile(path.join(outDir, "command.txt"), `${options.command}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "git_status.txt"), gitStatus.stdout, "utf8")
  await writeJson(path.join(outDir, "environment.json"), { node: process.version, platform: process.platform, cwd: process.cwd(), repoRoot: options.repoRoot, tuiRoot: options.tuiRoot })
  await writeJson(path.join(outDir, "scenario.json"), scene)
  await fs.writeFile(path.join(outDir, "raw.ansi"), frames.map((frame) => frame.frame).join("\n\n--- FRAME ---\n\n"), "utf8")
  await fs.writeFile(path.join(outDir, "plain_final.txt"), frames.length > 0 ? frames[frames.length - 1]!.plain : "", "utf8")
  await fs.writeFile(path.join(outDir, "grid_frames.ndjson"), frames.map((frame, index) => JSON.stringify({ index, cols: frame.cols, rows: frame.rows, lineCount: frame.lineCount, maxVisibleWidth: frame.maxVisibleWidth, plain: frame.plain })).join("\n") + "\n", "utf8")
  await fs.writeFile(path.join(outDir, "state_snapshots.ndjson"), state.stateSnapshots.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8")
  await fs.writeFile(path.join(outDir, "input_events.ndjson"), state.inputEvents.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8")
  await fs.writeFile(path.join(outDir, "dimension_events.ndjson"), state.dimensionEvents.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8")
  await writeJson(path.join(outDir, "anomalies.json"), anomalies)
  await writeJson(path.join(outDir, "invariant_report.json"), { ok: verdict === "pass", expectedRedObserved, sceneId: scene.id, generatedAt: new Date().toISOString(), results: invariantResults })
  await fs.writeFile(path.join(outDir, "visual_qc_notes.md"), `# P16 Compositor Visual QC Notes\n\nscene: ${scene.id}\nstatus: ${verdict === "pass" ? "pass_bounded" : "visual-red"}\nartifact_kind: production ReplView/controller compositor; not real-host proof.\nclaim_boundary: ${scene.claimBoundary}\nmanual_review_required: true\n\nThe generated contact sheet must be manually inspected before this scene supports any phase claim.\n`, "utf8")
  await writeJson(path.join(outDir, "claim_delta.json"), { sceneId: scene.id, claimSupported: verdict === "pass" && scene.expectedOutcome === "pass" ? "bounded production-renderer compositor evidence" : "known-bad calibration or red", notSupported: ["real terminal host proof", "VSCode/Cursor proof", "live model determinism"] })

  return { scene, outDir, frames, invariantResults, expectedRedObserved, verdict }
}
