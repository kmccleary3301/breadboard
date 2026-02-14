import { promises as fs } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
import { normalizeSessionEvent } from "../src/repl/transcript/normalizeSessionEvent.js"
import { buildTodoPreviewModel } from "../src/repl/components/replView/composer/todoPreview.js"

type CliOptions = {
  inputPath: string
  configPath: string
  outPath: string | null
  strict: boolean
  maxItems: number
  strategy: "first_n" | "incomplete_first" | "active_first"
  showHiddenCount: boolean
  style: "minimal" | "nice" | "dense"
  cols: number | null
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let inputPath = ""
  let configPath = "agent_configs/codex_cli_gpt51mini_e4_live.yaml"
  let outPath: string | null = null
  let strict = false
  let maxItems = 7
  let strategy: CliOptions["strategy"] = "active_first"
  let showHiddenCount = true
  let style: CliOptions["style"] = "dense"
  let cols: number | null = 120

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--input":
        inputPath = args[++i] ?? inputPath
        break
      case "--config":
        configPath = args[++i] ?? configPath
        break
      case "--out":
        outPath = args[++i] ?? outPath
        break
      case "--strict":
        strict = true
        break
      case "--max-items": {
        const raw = args[++i]
        const parsed = raw != null ? Number(raw) : Number.NaN
        if (Number.isFinite(parsed) && parsed > 0) maxItems = Math.floor(parsed)
        break
      }
      case "--strategy": {
        const raw = (args[++i] ?? "").trim()
        if (raw === "first_n" || raw === "incomplete_first" || raw === "active_first") {
          strategy = raw
        }
        break
      }
      case "--show-hidden-count":
        showHiddenCount = true
        break
      case "--hide-hidden-count":
        showHiddenCount = false
        break
      case "--style": {
        const raw = (args[++i] ?? "").trim()
        if (raw === "minimal" || raw === "nice" || raw === "dense") {
          style = raw
        }
        break
      }
      case "--cols": {
        const raw = args[++i]
        const parsed = raw != null ? Number(raw) : Number.NaN
        cols = Number.isFinite(parsed) && parsed >= 10 ? Math.floor(parsed) : null
        break
      }
      default:
        break
    }
  }

  if (!inputPath) {
    throw new Error("Usage: tsx scripts/todo_preview_churn_gate.ts --input <events.jsonl> [--strict]")
  }

  return { inputPath, configPath, outPath, strict, maxItems, strategy, showHiddenCount, style, cols }
}

const normalizePath = (value: string, baseDir: string): string => {
  if (path.isAbsolute(value)) return value
  return path.resolve(baseDir, value)
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value != null

const parseEvent = (raw: string): Record<string, unknown> | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== "object") return null
  if (!parsed.type) return null
  const normalized = normalizeSessionEvent(parsed as Record<string, unknown>) as Record<string, unknown> | null
  if (!normalized) return null
  if (isRecord((parsed as any).payload)) {
    return { ...normalized, payload: (parsed as any).payload }
  }
  return normalized
}

const isTodoUpdateEvent = (event: Record<string, unknown>): boolean => {
  if (!isRecord(event.payload)) return false
  const payload = event.payload as Record<string, unknown>
  if (Object.prototype.hasOwnProperty.call(payload, "todo") && payload.todo != null) return true
  const toolNameRaw =
    (typeof payload.tool_name === "string" ? payload.tool_name : null) ??
    (typeof payload.toolName === "string" ? payload.toolName : null) ??
    (typeof payload.name === "string" ? payload.name : null) ??
    ""
  const toolName = toolNameRaw.trim().toLowerCase()
  if (toolName.includes("todowrite")) return true
  if (Array.isArray(payload.todos) && toolName.includes("todo")) return true
  return false
}

const serializeModel = (model: ReturnType<typeof buildTodoPreviewModel>): string => {
  if (!model) return ""
  const lines = []
  if (model.headerLine) lines.push(model.headerLine)
  for (const item of model.items) lines.push(item.label)
  return lines.join("\n")
}

const main = async () => {
  const options = parseArgs()
  const absInput = normalizePath(options.inputPath, process.cwd())
  const absConfig = normalizePath(options.configPath, process.cwd())
  const raw = await fs.readFile(absInput, "utf8")
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)

  const controller = new ReplSessionController({
    configPath: absConfig,
    workspace: null,
    model: null,
    remotePreference: null,
    permissionMode: null,
  }) as any

  let prev = ""
  let churn = 0
  let todoEvents = 0
  const churnEvents: Array<{ seq?: number | null; type?: string | null }> = []

  for (const line of lines) {
    const evt = parseEvent(line)
    if (!evt) continue
    controller.applyEvent(evt)
    const model = buildTodoPreviewModel(controller.getState().todoStore, {
      maxItems: options.maxItems,
      strategy: options.strategy,
      showHiddenCount: options.showHiddenCount,
      style: options.style,
      cols: options.cols,
      stale: controller.getState().todoScopeStale,
      scopeKey: controller.getState().todoScopeKey,
      scopeLabel: controller.getState().todoScopeLabel,
    })
    const current = serializeModel(model)
    const isTodo = isTodoUpdateEvent(evt)
    if (isTodo) {
      todoEvents += 1
      prev = current
      continue
    }
    if (current !== prev) {
      churn += 1
      churnEvents.push({
        seq: typeof evt.seq === "number" ? evt.seq : null,
        type: typeof evt.type === "string" ? evt.type : null,
      })
      prev = current
    }
  }

  const result = {
    input: absInput,
    churn,
    todoEvents,
    churnEvents,
  }
  const outJson = `${JSON.stringify(result, null, 2)}\n`
  if (options.outPath) {
    const absOut = normalizePath(options.outPath, process.cwd())
    await fs.mkdir(path.dirname(absOut), { recursive: true })
    await fs.writeFile(absOut, outJson, "utf8")
  }

  console.log(outJson.trimEnd())
  process.exit(options.strict && churn > 0 ? 2 : 0)
}

main().catch((error) => {
  console.error("[todo_preview_churn_gate] failed:", error)
  process.exit(1)
})
