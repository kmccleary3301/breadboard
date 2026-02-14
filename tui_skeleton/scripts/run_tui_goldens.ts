import { promises as fs } from "node:fs"
import path from "node:path"
import yaml from "yaml"
import { renderGridFromFrames } from "../tools/tty/vtgrid.js"
import { normalizeSessionEvent } from "../src/repl/transcript/normalizeSessionEvent.js"

type RenderOptions = {
  include_header?: boolean
  include_status?: boolean
  include_hints?: boolean
  include_model_menu?: boolean
  include_composer?: boolean
  include_todo_preview?: boolean
  todo_preview_max_items?: number
  todo_preview_strategy?: string
  todo_preview_show_hidden_count?: boolean
  colors?: boolean
  ascii_only?: boolean
  max_width?: number
  max_height?: number
  color_mode?: string
  render_profile?: "default" | "claude"
  view_prefs?: {
    collapseMode?: string
    virtualization?: string
    richMarkdown?: boolean
    toolRail?: boolean
    toolInline?: boolean
    rawStream?: boolean
    showReasoning?: boolean
    diffLineNumbers?: boolean
  }
}

type ScenarioConfig = {
  id: string
  input: string
  render?: RenderOptions
}

type Manifest = {
  version: number
  defaults?: {
    render?: RenderOptions
  }
  scenarios: ScenarioConfig[]
}

type CliOptions = {
  manifestPath: string
  outputRoot: string
  configPath: string
  maxWidth?: number
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let manifestPath = "misc/tui_goldens/manifests/tui_goldens.yaml"
  let outputRoot = "misc/tui_goldens/artifacts"
  let configPath = "agent_configs/codex_cli_gpt51mini_e4_live.yaml"
  let maxWidth: number | undefined

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--manifest":
        manifestPath = args[++i] ?? manifestPath
        break
      case "--out":
        outputRoot = args[++i] ?? outputRoot
        break
      case "--config":
        configPath = args[++i] ?? configPath
        break
      case "--max-width": {
        const raw = args[++i]
        if (raw != null) {
          const parsed = Number(raw)
          if (Number.isFinite(parsed) && parsed > 0) {
            maxWidth = Math.floor(parsed)
          }
        }
        break
      }
      default:
        break
    }
  }

  return {
    manifestPath,
    outputRoot,
    configPath,
    maxWidth,
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

const readManifest = async (manifestPath: string): Promise<Manifest> => {
  const raw = await fs.readFile(manifestPath, "utf8")
  const parsed = yaml.parse(raw) as Manifest
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.scenarios)) {
    throw new Error(`Invalid manifest: ${manifestPath}`)
  }
  return parsed
}

const normalizePath = (value: string, baseDir: string): string => {
  if (path.isAbsolute(value)) return value
  return path.resolve(baseDir, value)
}

const renderScenario = async (
  scenario: ScenarioConfig,
  options: CliOptions,
  baseDir: string,
  defaults: RenderOptions,
): Promise<string> => {
  const { ReplSessionController } = await import("../src/commands/repl/controller.js")
  const { renderStateToText } = await import("../src/commands/repl/renderText.js")
  const inputPath = normalizePath(scenario.input, baseDir)
  await fs.access(inputPath)
  const raw = await fs.readFile(inputPath, "utf8")
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)
  const controller = new ReplSessionController({
    configPath: options.configPath,
    workspace: null,
    model: null,
    remotePreference: null,
    permissionMode: null,
  })
  const render = { ...defaults, ...(scenario.render ?? {}) }
  if (render.view_prefs && Object.keys(render.view_prefs).length > 0) {
    ;(controller as any).updateViewPrefs(render.view_prefs)
  }

  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    ;(controller as any).applyEvent(event)
  }

  const text = renderStateToText(controller.getState(), {
    includeHeader: render.include_header !== false,
    includeStatus: render.include_status !== false,
    includeHints: render.include_hints !== false,
    includeModelMenu: render.include_model_menu !== false,
    includeComposer: render.include_composer === true,
    includeTodoPreview: render.include_todo_preview === true,
    todoPreviewMaxItems: typeof render.todo_preview_max_items === "number" ? render.todo_preview_max_items : undefined,
    todoPreviewStrategy: typeof render.todo_preview_strategy === "string" ? render.todo_preview_strategy : undefined,
    todoPreviewShowHiddenCount: render.todo_preview_show_hidden_count === true,
    colors: render.colors === true,
    asciiOnly: render.ascii_only !== false,
    maxWidth: options.maxWidth ?? (typeof render.max_width === "number" ? render.max_width : undefined),
    colorMode: typeof render.color_mode === "string" ? render.color_mode : undefined,
    renderProfile: render.render_profile ?? "default",
  })
  return text
}

const renderTextToGrid = (text: string, options: RenderOptions) => {
  const cols = typeof options.max_width === "number" ? options.max_width : 120
  const rows =
    typeof options.max_height === "number"
      ? options.max_height
      : Number(process.env.BREADBOARD_TUI_FRAME_HEIGHT ?? "40")
  const frames = [{ timestamp: Date.now(), chunk: `${text}\n` }]
  return {
    rows,
    cols,
    ...renderGridFromFrames(frames, Math.max(1, rows), Math.max(10, cols)),
  }
}

const main = async () => {
  const options = parseArgs()
  if (!process.env.FORCE_COLOR && !process.env.NO_COLOR) {
    process.env.FORCE_COLOR = "1"
  }
  const manifestPath = normalizePath(options.manifestPath, process.cwd())
  const manifest = await readManifest(manifestPath)
  const baseDir = path.dirname(manifestPath)
  const renderDefaults = manifest.defaults?.render ?? {}
  const outRoot = path.resolve(options.outputRoot, `run-${Date.now()}`)
  await fs.mkdir(outRoot, { recursive: true })

  for (const scenario of manifest.scenarios) {
    const render = { ...renderDefaults, ...(scenario.render ?? {}) }
    const rendered = await renderScenario(scenario, options, baseDir, renderDefaults)
    const scenarioDir = path.join(outRoot, scenario.id)
    await fs.mkdir(scenarioDir, { recursive: true })
    const outPath = path.join(scenarioDir, "render.txt")
    await fs.writeFile(outPath, rendered, "utf8")
    const gridArtifacts = renderTextToGrid(rendered, render)
    const gridPath = path.join(scenarioDir, "frame.grid.json")
    await fs.writeFile(
      gridPath,
      `${JSON.stringify(
        {
          rows: gridArtifacts.rows,
          cols: gridArtifacts.cols,
          grid: gridArtifacts.grid,
          activeBuffer: gridArtifacts.activeBuffer,
          normalGrid: gridArtifacts.normalGrid,
          alternateGrid: gridArtifacts.alternateGrid,
          timestamp: Date.now(),
        },
        null,
        2,
      )}\n`,
      "utf8",
    )
    console.log(`[tui_goldens] ${scenario.id} -> ${outPath}`)
  }
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("[tui_goldens] failed:", error)
    process.exit(1)
  })
