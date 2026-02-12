import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { renderGridFromFrames } from "../tools/tty/vtgrid.js"
import { normalizeSessionEvent } from "../src/repl/transcript/normalizeSessionEvent.js"

type RawEvent = Record<string, unknown>

interface Args {
  outDir: string
  baselineDir: string | null
  fixtures: string[]
  rows: number
  cols: number
  strict: boolean
  configPath: string
}

const DEFAULT_FIXTURES = [
  "src/commands/repl/__tests__/fixtures/assistant_tool_interleave.jsonl",
  "src/commands/repl/__tests__/fixtures/multi_turn_interleave.jsonl",
  "src/commands/repl/__tests__/fixtures/system_notice_tool_error.jsonl",
  "src/commands/repl/__tests__/fixtures/tool_call_result.jsonl",
  "src/commands/repl/__tests__/fixtures/tool_display_head_tail.jsonl",
  "src/commands/repl/__tests__/fixtures/runtime_plan_path.jsonl",
  "src/commands/repl/__tests__/fixtures/inline_thinking_permission_tool_answer.jsonl",
]

const parseArgs = (): Args => {
  const argv = process.argv.slice(2)
  let outDir = path.resolve("../artifacts/runtime_triage")
  let baselineDir: string | null = null
  let fixtures = [...DEFAULT_FIXTURES]
  let rows = Number(process.env.BREADBOARD_TUI_FRAME_HEIGHT ?? "40")
  let cols = Number(process.env.BREADBOARD_TUI_FRAME_WIDTH ?? "120")
  let strict = false
  let configPath = path.resolve("../agent_configs/codex_cli_gpt51mini_e4_live.yaml")
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--out-dir") outDir = path.resolve(argv[++i] ?? outDir)
    else if (arg === "--baseline-dir") baselineDir = path.resolve(argv[++i] ?? "")
    else if (arg === "--fixtures") {
      fixtures = (argv[++i] ?? "")
        .split(",")
        .map((value) => value.trim())
        .filter((value) => value.length > 0)
    } else if (arg === "--rows") rows = Number(argv[++i] ?? rows)
    else if (arg === "--cols") cols = Number(argv[++i] ?? cols)
    else if (arg === "--strict") strict = true
    else if (arg === "--config") configPath = path.resolve(argv[++i] ?? configPath)
  }
  return {
    outDir,
    baselineDir,
    fixtures,
    rows: Number.isFinite(rows) ? Math.max(1, Math.floor(rows)) : 40,
    cols: Number.isFinite(cols) ? Math.max(10, Math.floor(cols)) : 120,
    strict,
    configPath,
  }
}

const parseEvent = (raw: string): RawEvent | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  const normalize = (input: Record<string, unknown>): RawEvent | null => {
    const normalized = normalizeSessionEvent(input) as RawEvent | null
    if (!normalized) return null
    const payload = input.payload
    if (payload && typeof payload === "object") {
      return { ...normalized, payload }
    }
    return normalized
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") return normalize(parsed.event as RawEvent)
    if (typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") return normalize(inner as RawEvent)
      } catch {
        return null
      }
    }
    if (parsed.type) return normalize(parsed as RawEvent)
  }
  return null
}

const readFixtureEvents = (fixturePath: string): RawEvent[] =>
  readFileSync(path.resolve(fixturePath), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map(parseEvent)
    .filter((event): event is RawEvent => Boolean(event))

const renderFixture = async (
  fixturePath: string,
  options: { rows: number; cols: number; configPath: string },
): Promise<{ renderText: string; grid: any }> => {
  const { ReplSessionController } = await import("../src/commands/repl/controller.js")
  const { renderStateToText } = await import("../src/commands/repl/renderText.js")
  const controller = new ReplSessionController({
    configPath: options.configPath,
    workspace: null,
    model: null,
    remotePreference: null,
    permissionMode: null,
  }) as unknown as {
    applyEvent: (evt: RawEvent) => void
    getState: () => any
    liveSlots?: Map<string, unknown>
    liveSlotTimers?: Map<string, NodeJS.Timeout>
  }
  for (const evt of readFixtureEvents(fixturePath)) {
    controller.applyEvent(evt)
  }
  try {
    controller.liveSlots?.clear?.()
    controller.liveSlotTimers?.forEach?.((timer: NodeJS.Timeout) => clearTimeout(timer))
    controller.liveSlotTimers?.clear?.()
  } catch {
    // best effort cleanup for deterministic render
  }
  const renderText = renderStateToText(controller.getState(), {
    includeHeader: false,
    includeStatus: true,
    includeHints: false,
    includeModelMenu: false,
    colors: false,
    asciiOnly: true,
    maxWidth: options.cols,
  })
  const grid = renderGridFromFrames([{ timestamp: Date.now(), chunk: `${renderText}\n` }], options.rows, options.cols)
  return { renderText, grid }
}

const main = async (): Promise<void> => {
  const args = parseArgs()
  mkdirSync(args.outDir, { recursive: true })
  const diffDir = path.join(args.outDir, "_diffs")
  mkdirSync(diffDir, { recursive: true })

  const scenarios: Array<{
    name: string
    fixture: string
    renderPath: string
    gridPath: string
    changed: boolean
  }> = []

  for (const fixture of args.fixtures) {
    const absoluteFixture = path.resolve(fixture)
    const name = path.basename(absoluteFixture).replace(/\.jsonl$/i, "")
    const scenarioDir = path.join(args.outDir, name)
    mkdirSync(scenarioDir, { recursive: true })
    const { renderText, grid } = await renderFixture(absoluteFixture, {
      rows: args.rows,
      cols: args.cols,
      configPath: args.configPath,
    })
    const renderPath = path.join(scenarioDir, "render.txt")
    const gridPath = path.join(scenarioDir, "frame.grid.json")
    writeFileSync(renderPath, `${renderText}\n`, "utf8")
    writeFileSync(
      gridPath,
      `${JSON.stringify(
        {
          rows: args.rows,
          cols: args.cols,
          grid: grid.grid,
          activeBuffer: grid.activeBuffer,
          normalGrid: grid.normalGrid,
          alternateGrid: grid.alternateGrid,
          timestamp: Date.now(),
        },
        null,
        2,
      )}\n`,
      "utf8",
    )
    let changed = false
    if (args.baselineDir) {
      const baselineRender = path.join(args.baselineDir, name, "render.txt")
      try {
        const previous = readFileSync(baselineRender, "utf8")
        changed = previous !== `${renderText}\n`
      } catch {
        changed = true
      }
    }
    scenarios.push({
      name,
      fixture: absoluteFixture,
      renderPath,
      gridPath,
      changed,
    })
  }

  const changedScenarios = scenarios.filter((entry) => entry.changed).map((entry) => entry.name)
  const diffIndex = {
    total: scenarios.length,
    changedCount: changedScenarios.length,
    changedScenarios,
  }
  writeFileSync(path.join(diffDir, "index.json"), `${JSON.stringify(diffIndex, null, 2)}\n`, "utf8")

  const index = {
    generatedAt: new Date().toISOString(),
    rows: args.rows,
    cols: args.cols,
    fixtures: args.fixtures,
    scenarios,
    diff: diffIndex,
  }
  writeFileSync(path.join(args.outDir, "index.json"), `${JSON.stringify(index, null, 2)}\n`, "utf8")
  console.log(`[runtime_quick_triage_snapshots] wrote ${args.outDir} (${scenarios.length} scenarios)`)

  if (args.strict && args.baselineDir && changedScenarios.length > 0) {
    process.exit(1)
  }
  process.exit(0)
}

main().catch((error) => {
  console.error("[runtime_quick_triage_snapshots] failed:", error)
  process.exit(1)
})
