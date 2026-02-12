import { promises as fs } from "node:fs"
import path from "node:path"
import { renderGridFromFrames } from "../tools/tty/vtgrid.js"
import { normalizeSessionEvent } from "../src/repl/transcript/normalizeSessionEvent.js"

type CliOptions = {
  inputPath: string
  outputPath: string
  includeHeader: boolean
  includeStatus: boolean
  includeHints: boolean
  includeModelMenu: boolean
  colors: boolean
  asciiOnly: boolean
  includeLiveSlots: boolean
  configPath: string
  rows: number
  cols: number
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let inputPath = ""
  let outputPath = ""
  let includeHeader = false
  let includeStatus = false
  let includeHints = false
  let includeModelMenu = false
  let colors = false
  let asciiOnly = true
  let includeLiveSlots = false
  let configPath = path.resolve("agent_configs/codex_cli_gpt51mini_e4_live.yaml")
  let rows = Number(process.env.BREADBOARD_TUI_FRAME_HEIGHT ?? "40")
  let cols = Number(process.env.BREADBOARD_TUI_FRAME_WIDTH ?? "120")

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--in":
        inputPath = args[++i] ?? ""
        break
      case "--out":
        outputPath = args[++i] ?? ""
        break
      case "--config":
        configPath = args[++i] ?? configPath
        break
      case "--include-header":
        includeHeader = true
        break
      case "--include-status":
        includeStatus = true
        break
      case "--include-hints":
        includeHints = true
        break
      case "--include-model-menu":
        includeModelMenu = true
        break
      case "--colors":
        colors = true
        break
      case "--unicode":
        asciiOnly = false
        break
      case "--include-live":
        includeLiveSlots = true
        break
      case "--rows":
        rows = Number(args[++i] ?? rows)
        break
      case "--cols":
        cols = Number(args[++i] ?? cols)
        break
      default:
        break
    }
  }

  if (!inputPath) {
    throw new Error("Usage: tsx scripts/render_events_jsonl_grid.ts --in <events.jsonl> [--out <file>]")
  }
  if (!outputPath) {
    outputPath = inputPath.replace(/\.jsonl$/i, ".frame.grid.json")
  }
  return {
    inputPath,
    outputPath,
    includeHeader,
    includeStatus,
    includeHints,
    includeModelMenu,
    colors,
    asciiOnly,
    includeLiveSlots,
    configPath,
    rows,
    cols,
  }
}

const attachPayload = (
  normalized: Record<string, unknown> | null,
  source: Record<string, unknown>,
): Record<string, unknown> | null => {
  if (!normalized) return null
  const payload = source.payload
  if (payload && typeof payload === "object") {
    return { ...normalized, payload }
  }
  return normalized
}

const parseEvent = (raw: string): Record<string, unknown> | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") {
      return attachPayload(
        normalizeSessionEvent(parsed.event as Record<string, unknown>) as Record<string, unknown>,
        parsed.event as Record<string, unknown>,
      )
    }
    if (parsed.data && typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") {
          return attachPayload(
            normalizeSessionEvent(inner as Record<string, unknown>) as Record<string, unknown>,
            inner as Record<string, unknown>,
          )
        }
      } catch {
        return null
      }
    }
    if (parsed.type) {
      return attachPayload(
        normalizeSessionEvent(parsed as Record<string, unknown>) as Record<string, unknown>,
        parsed as Record<string, unknown>,
      )
    }
  }
  return null
}

const main = async () => {
  const options = parseArgs()
  if (!process.env.FORCE_COLOR) {
    process.env.FORCE_COLOR = options.colors ? "1" : "0"
  }
  const { ReplSessionController } = await import("../src/commands/repl/controller.js")
  const { renderStateToText } = await import("../src/commands/repl/renderText.js")
  const raw = await fs.readFile(options.inputPath, "utf8")
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)
  const controller = new ReplSessionController({
    configPath: options.configPath,
    workspace: null,
    model: null,
    remotePreference: null,
    permissionMode: null,
  })

  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    ;(controller as any).applyEvent(event)
  }
  if (!options.includeLiveSlots) {
    try {
      ;(controller as any).liveSlots?.clear?.()
      ;(controller as any).liveSlotTimers?.forEach?.((timer: NodeJS.Timeout) => clearTimeout(timer))
      ;(controller as any).liveSlotTimers?.clear?.()
    } catch {
      // ignore
    }
  }

  const text = renderStateToText(controller.getState(), {
    includeHeader: options.includeHeader,
    includeStatus: options.includeStatus,
    includeHints: options.includeHints,
    includeModelMenu: options.includeModelMenu,
    colors: options.colors,
    asciiOnly: options.asciiOnly,
    maxWidth: Number.isFinite(options.cols) ? options.cols : undefined,
  })

  const frames = [{ timestamp: Date.now(), chunk: `${text}\n` }]
  const rows = Number.isFinite(options.rows) ? Math.max(1, Math.floor(options.rows)) : 40
  const cols = Number.isFinite(options.cols) ? Math.max(10, Math.floor(options.cols)) : 120
  const grid = renderGridFromFrames(frames, rows, cols)

  const payload = {
    rows,
    cols,
    grid: grid.grid,
    activeBuffer: grid.activeBuffer,
    normalGrid: grid.normalGrid,
    alternateGrid: grid.alternateGrid,
    timestamp: Date.now(),
  }

  await fs.writeFile(options.outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8")
  console.log(`[render_events_jsonl_grid] wrote ${options.outputPath}`)
}

main().catch((error) => {
  console.error("[render_events_jsonl_grid] failed:", error)
  process.exit(1)
})
