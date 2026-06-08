import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { ensureDir, writeJson } from "./reports/artifactBundle"
import type { Scenario, ScenarioStep } from "./schema"

const args = process.argv.slice(2)
let captureDir: string | undefined
let outPath: string | undefined
for (let i = 0; i < args.length; i += 1) {
  if (args[i] === "--capture") captureDir = args[++i]
  else if (args[i] === "--out") outPath = args[++i]
}
if (!captureDir) {
  console.error("Usage: tsx tools/scenario-runtime/replayCaptureCli.ts --capture <capture-dir> [--out scenario.json]")
  process.exit(2)
}
const absCapture = path.isAbsolute(captureDir) ? captureDir : path.join(process.cwd(), captureDir)
const id = `replay_${path.basename(absCapture).replace(/[^a-zA-Z0-9_.-]/g, "_")}`

const readJsonIfExists = async <T>(target: string): Promise<T | null> => {
  try {
    return JSON.parse(await fs.readFile(target, "utf8")) as T
  } catch {
    return null
  }
}

const readNdjsonIfExists = async <T>(target: string): Promise<T[]> => {
  try {
    const raw = await fs.readFile(target, "utf8")
    return raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line) as T)
  } catch {
    return []
  }
}

const eventToScenarioStep = (raw: unknown, index: number): ScenarioStep | null => {
  if (!raw || typeof raw !== "object") return null
  const event = (raw as { event?: unknown }).event
  const delayMs = typeof (raw as { delayMs?: unknown }).delayMs === "number" ? (raw as { delayMs: number }).delayMs : 0
  if (!event || typeof event !== "object") return delayMs > 0 ? { kind: "wait", ms: delayMs } : null
  const typed = event as { type?: unknown; payload?: Record<string, unknown>; turn?: unknown }
  const type = String(typed.type ?? "")
  const payload = typed.payload ?? {}
  const turn = typeof typed.turn === "number" ? typed.turn : undefined
  const streamId = `capture-s${turn ?? 1}`

  switch (type) {
    case "turn_start":
      return { kind: "engine", event: { kind: "turn_start", payload } }
    case "assistant.message.start":
      return { kind: "assistant", event: { kind: "start", streamId, messageId: typeof payload.message_id === "string" ? payload.message_id : `capture-message-${index}`, turn } }
    case "assistant.message.delta":
      return { kind: "assistant", event: { kind: "delta", streamId, text: String(payload.delta ?? ""), delayMs: delayMs || undefined, turn } }
    case "assistant.message.end":
      return { kind: "assistant", event: { kind: "complete", streamId, finishReason: typeof payload.finish_reason === "string" ? payload.finish_reason : undefined, turn } }
    case "tool_call":
      return { kind: "tool", event: { kind: "start", toolId: String(payload.call_id ?? `capture-tool-${index}`), name: String(payload.tool_name ?? "tool"), args: payload.args, turn } }
    case "tool.exec.stdout.delta":
      return { kind: "tool", event: { kind: "stdout", toolId: String(payload.call_id ?? `capture-tool-${index}`), text: String(payload.delta ?? ""), turn } }
    case "tool.exec.stderr.delta":
      return { kind: "tool", event: { kind: "stderr", toolId: String(payload.call_id ?? `capture-tool-${index}`), text: String(payload.delta ?? ""), turn } }
    case "tool.exec.end":
      return { kind: "tool", event: { kind: "result", toolId: String(payload.call_id ?? `capture-tool-${index}`), status: Number(payload.exit_code ?? 0) === 0 ? "ok" : "error", turn } }
    case "completion":
    case "run_finished":
      return { kind: "engine", event: { kind: type, payload } }
    default:
      return null
  }
}

const buildStructuredReplay = async (): Promise<Scenario | null> => {
  const sourceScenario = await readJsonIfExists<Scenario>(path.join(absCapture, "scenario.json"))
  const manifest = await readJsonIfExists<Record<string, unknown>>(path.join(absCapture, "manifest.json"))
  const replay = await readJsonIfExists<{ steps?: unknown[] }>(path.join(absCapture, "engine_events.replay.json"))
  if (!sourceScenario || !manifest || !Array.isArray(replay?.steps)) return null

  const droppedEvents: string[] = []
  const timeline: ScenarioStep[] = []
  const expectedPrompt = typeof manifest.expectedPrompt === "string" ? manifest.expectedPrompt : undefined
  if (expectedPrompt) timeline.push({ kind: "submit", text: expectedPrompt })
  for (const [index, step] of replay.steps.entries()) {
    const converted = eventToScenarioStep(step, index)
    if (converted) timeline.push(converted)
    else droppedEvents.push(`engine_events.replay.steps[${index}]`)
  }

  const expectedAssistantText =
    typeof manifest.expectedAssistantText === "string"
      ? manifest.expectedAssistantText
      : timeline
          .filter((step): step is Extract<ScenarioStep, { kind: "assistant" }> => step.kind === "assistant")
          .map((step) => (step.event.kind === "delta" ? step.event.text : ""))
          .join("")
          .trim()
  if (expectedAssistantText) timeline.push({ kind: "waitFor", target: { text: expectedAssistantText }, timeoutMs: 60_000 })

  const resizeEvents = await readNdjsonIfExists<{ cols?: unknown; rows?: unknown }>(path.join(absCapture, "resize_events.ndjson"))
  for (const [index, resize] of resizeEvents.entries()) {
    if (typeof resize.cols === "number" && typeof resize.rows === "number") {
      timeline.push({ kind: "resize", cols: resize.cols, rows: resize.rows, settleMs: 200 })
    } else {
      droppedEvents.push(`resize_events.ndjson[${index}]`)
    }
  }
  timeline.push({ kind: "checkpoint", id: "capture-replay-final" })

  return {
    ...sourceScenario,
    id,
    title: `Structured replay for ${sourceScenario.id} from ${path.basename(absCapture)}`,
    campaign: "p14-v6-replay-live-capture",
    claimScope: "pty",
    terminal: {
      ...sourceScenario.terminal,
      lanes: ["pty"],
      captureGrid: true,
      captureScrollback: true,
    },
    timeline,
    sourceCapture: {
      captureId: path.basename(absCapture),
      artifactRoot: absCapture,
      conversionVersion: 2,
      droppedEvents,
      timingMode: "logical-checkpoint",
    },
  }
}

const scenario = (await buildStructuredReplay()) ?? {
  schemaVersion: 1,
  id,
  title: `Generated replay for ${path.basename(absCapture)}`,
  claimScope: "pty",
  productionEquivalence: {
    required: true,
    claimTier: "S2",
    allowedSyntheticLayers: ["model", "tool", "time", "workspace"],
    forbiddenBypass: ["renderer", "transcript-store", "composer", "scrollback-feed"]
  },
  environment: { prelaunchShellHistory: [{ id: "capture-pre", text: `BB_CAPTURE_${id}` }] },
  launch: { mode: "classic", engineMode: "test-owned", startupWait: { composerReady: true, timeoutMs: 20000 }, timeoutMs: 90000 },
  terminal: { lanes: ["pty"], initial: { cols: 100, rows: 30 }, captureGrid: true, captureScrollback: true },
  sourceCapture: { captureId: path.basename(absCapture), artifactRoot: absCapture, conversionVersion: 1, droppedEvents: [], timingMode: "logical-checkpoint" },
  timeline: [
    { kind: "submit", text: "Replay captured session smoke." },
    { kind: "assistant", event: { kind: "start", streamId: "s1" } },
    { kind: "assistant", event: { kind: "delta", streamId: "s1", text: "Replay conversion placeholder completed." } },
    { kind: "assistant", event: { kind: "complete", streamId: "s1" } },
    { kind: "waitFor", target: { text: "Replay conversion placeholder completed." }, timeoutMs: 30000 },
    { kind: "checkpoint", id: "replay-final" }
  ],
  invariants: [
    { id: "GLOBAL-HOST-HISTORY-PRESERVED", severity: "blocker" },
    { id: "GLOBAL-NO-RAW-ANSI", severity: "blocker" },
    { id: "GLOBAL-NO-DUPLICATE-PROMPT", severity: "blocker" },
    { id: "GLOBAL-COMPOSER-VISIBLE", severity: "blocker" }
  ],
  artifacts: { required: ["manifest", "raw", "grid", "state", "invariant-report"] }
}
const target = outPath ? (path.isAbsolute(outPath) ? outPath : path.join(process.cwd(), outPath)) : path.join(process.cwd(), "scenarios/replay", `${id}.json`)
await ensureDir(path.dirname(target))
await writeJson(target, scenario)
console.log(`[scenario:replay-capture] wrote ${target}`)
