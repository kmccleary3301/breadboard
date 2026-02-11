import { promises as fs } from "node:fs"
import path from "node:path"
import { resolveBreadboardPath } from "../utils/paths.js"

export interface ToolDisplayRuleInfo {
  readonly id: string
  readonly match?: Record<string, unknown>
  readonly priority?: number
  readonly category?: string
}

const DEFAULTS_PATH = "implementations/tool_display_defaults.json"

const parseYaml = async (text: string): Promise<any | null> => {
  try {
    const mod = await import("yaml")
    const parse = (mod as any).parse ?? (mod as any).default?.parse
    if (typeof parse === "function") {
      return parse(text)
    }
  } catch {
    // ignore
  }
  return null
}

const parseConfigFile = async (filePath: string): Promise<any | null> => {
  const raw = await fs.readFile(filePath, "utf8")
  const trimmed = raw.trim()
  if (!trimmed) return null
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed)
    } catch {
      // fall through
    }
  }
  return await parseYaml(raw)
}

const normalizeRules = (raw: any, basePriority: number): ToolDisplayRuleInfo[] => {
  const rules: ToolDisplayRuleInfo[] = []
  if (!raw) return rules
  if (Array.isArray(raw)) {
    raw.forEach((entry) => {
      if (entry && typeof entry === "object") {
        const id = String((entry as any).id ?? (entry as any).name ?? "rule")
        const priority = typeof (entry as any).priority === "number" ? (entry as any).priority : basePriority
        rules.push({
          id,
          match: (entry as any).match,
          priority,
          category: typeof (entry as any).category === "string" ? (entry as any).category : undefined,
        })
      }
    })
    return rules
  }
  if (raw && typeof raw === "object") {
    Object.entries(raw as Record<string, any>).forEach(([key, value]) => {
      if (["version", "rules", "redact", "defaults"].includes(key)) return
      if (!value || typeof value !== "object") return
      rules.push({
        id: key,
        match: { tool: key },
        priority: basePriority,
        category: typeof (value as any).category === "string" ? (value as any).category : undefined,
      })
    })
  }
  return rules
}

export const loadToolDisplayRules = async (configPath?: string | null): Promise<ToolDisplayRuleInfo[]> => {
  const defaultsPath = resolveBreadboardPath(DEFAULTS_PATH)
  const defaultsConfig = await parseConfigFile(defaultsPath).catch(() => null)
  const rules: ToolDisplayRuleInfo[] = []
  if (defaultsConfig && typeof defaultsConfig === "object") {
    const defaultRules = normalizeRules((defaultsConfig as any).rules ?? defaultsConfig, -10)
    rules.push(...defaultRules)
  }
  if (configPath) {
    const resolvedConfig = path.isAbsolute(configPath) ? configPath : resolveBreadboardPath(configPath)
    const config = await parseConfigFile(resolvedConfig).catch(() => null)
    if (config && typeof config === "object") {
      const ui = (config as any).ui
      const toolDisplay = ui?.tool_display
      const rulesList = normalizeRules(toolDisplay?.rules ?? toolDisplay, 50)
      rules.push(...rulesList)
    }
  }
  return rules
}
