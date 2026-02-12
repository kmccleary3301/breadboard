import type { CTreeSnapshot, ModelMenuItem, SkillEntry, PermissionRuleScope } from "../../../types.js"
import {
  ASCII_ONLY,
  CHALK,
  COLORS,
  COLUMN_SEPARATOR,
  DASH_GLYPH,
  DOT_SEPARATOR,
  ICONS,
  STAR_GLYPH,
  uiText,
} from "../theme.js"
import { formatCell } from "../utils/format.js"
import { highlightFuzzyLabel } from "../utils/text.js"

export const MODEL_PROVIDER_ORDER = [
  "openai",
  "anthropic",
  "google",
  "openrouter",
  "xai",
  "mistral",
  "meta",
  "cohere",
  "deepseek",
  "local",
  "other",
]
export const SKILL_GROUP_ORDER = ["language", "repo", "lint", "search", "docs", "misc"]
export const ALWAYS_ALLOW_SCOPE: PermissionRuleScope = "project"
export const DOUBLE_CTRL_C_WINDOW_MS = 1500

export const BOX_CHARS = ASCII_ONLY
  ? { tl: "+", tr: "+", bl: "+", br: "+", h: "-", v: "|" }
  : { tl: "╭", tr: "╮", bl: "╰", br: "╯", h: "─", v: "│" }

export const formatCtreeSummary = (snapshot?: CTreeSnapshot | null): string | null => {
  if (!snapshot) return null
  const snap = snapshot.snapshot as Record<string, unknown> | null | undefined
  const compiler = snapshot.compiler as Record<string, unknown> | null | undefined
  const collapse = snapshot.collapse as Record<string, unknown> | null | undefined
  const runner = snapshot.runner as Record<string, unknown> | null | undefined
  const hashSummary = (snapshot as any).hash_summary as Record<string, unknown> | null | undefined
  const nodeCount = typeof snap?.node_count === "number" ? snap.node_count : null
  const nodeHash = typeof snap?.node_hash === "string" ? snap.node_hash : null
  const summarySha = typeof hashSummary?.summary_sha256 === "string" ? hashSummary.summary_sha256 : null
  const compilerHashes =
    compiler && typeof compiler.hashes === "object" && compiler.hashes !== null
      ? (compiler.hashes as Record<string, unknown>)
      : null
  const compilerHash =
    typeof compilerHashes?.z3 === "string"
      ? compilerHashes.z3
      : typeof compilerHashes?.z2 === "string"
        ? compilerHashes.z2
        : typeof compilerHashes?.z1 === "string"
          ? compilerHashes.z1
          : typeof compiler?.z3 === "string"
            ? compiler.z3
            : typeof compiler?.z2 === "string"
              ? compiler.z2
              : typeof compiler?.z1 === "string"
                ? compiler.z1
                : null
  const collapsePolicy =
    collapse && typeof collapse.policy === "object" && collapse.policy !== null
      ? (collapse.policy as Record<string, unknown>)
      : null
  const policy = typeof collapsePolicy?.ordering === "string" ? collapsePolicy.ordering : typeof collapse?.policy === "string" ? collapse.policy : null
  const dropped = typeof collapsePolicy?.dropped === "number" ? collapsePolicy.dropped : typeof collapse?.dropped === "number" ? collapse.dropped : null
  const target = typeof collapsePolicy?.target === "number" ? collapsePolicy.target : null
  const runnerEnabled = typeof runner?.enabled === "boolean" ? runner.enabled : null
  const branches = Array.isArray(runner?.branches) ? runner?.branches.length : null
  const parts: string[] = []
  if (policy) parts.push(`policy ${policy}`)
  if (target != null) parts.push(`target ${target}`)
  if (nodeCount != null) parts.push(`${nodeCount} nodes`)
  if (nodeHash) parts.push(`hash ${nodeHash.slice(0, 8)}`)
  if (compilerHash) parts.push(`compiler ${compilerHash.slice(0, 8)}`)
  if (summarySha) parts.push(`summary ${summarySha.slice(0, 8)}`)
  if (dropped != null && dropped > 0) parts.push(`dropped ${dropped}`)
  if (runnerEnabled != null) {
    parts.push(runnerEnabled ? `branches ${branches ?? 0}` : "runner off")
  }
  if (parts.length === 0) return null
  return uiText(`CTree: ${parts.join(DOT_SEPARATOR)}`)
}

export const normalizeModeLabel = (value?: string | null): { label: string; color: string } => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "plan") return { label: "PLAN", color: COLORS.accent }
  if (normalized === "auto") return { label: "AUTO", color: COLORS.info }
  if (normalized && normalized !== "build") return { label: normalized.toUpperCase(), color: COLORS.info }
  return { label: "NORMAL", color: COLORS.info }
}

export const normalizePermissionLabel = (value?: string | null): { label: string; color: string } => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (!normalized || ["auto", "allow", "auto-accept", "auto_accept"].includes(normalized)) {
    return { label: "AUTO-ACCEPT", color: COLORS.success }
  }
  if (["prompt", "ask", "interactive"].includes(normalized)) {
    return { label: "PROMPT", color: COLORS.warning }
  }
  return { label: normalized.toUpperCase(), color: COLORS.warning }
}

export const normalizeProviderKey = (value: string | null | undefined): string => {
  const normalized = value?.trim().toLowerCase() ?? ""
  if (!normalized) return "other"
  if (normalized === "x-ai") return "xai"
  return normalized
}

export const formatProviderLabel = (value: string | null | undefined): string => {
  const normalized = normalizeProviderKey(value)
  switch (normalized) {
    case "openai":
      return "OpenAI"
    case "openrouter":
      return "OpenRouter"
    case "anthropic":
      return "Anthropic"
    case "google":
      return "Google"
    case "xai":
      return "xAI"
    case "mistral":
      return "Mistral"
    case "meta":
      return "Meta"
    case "cohere":
      return "Cohere"
    case "deepseek":
      return "DeepSeek"
    case "local":
      return "Local"
    case "other":
      return "Other"
    default: {
      const raw = value?.trim()
      if (!raw) return "Other"
      return raw
        .split(/[^a-z0-9]+/i)
        .filter((part) => part.length > 0)
        .map((part) => part[0].toUpperCase() + part.slice(1))
        .join(" ")
    }
  }
}

export const buildSkillKey = (skill: SkillEntry): string => `${skill.id}@${skill.version}`
export const isSkillSelected = (selected: Set<string>, skill: SkillEntry): boolean =>
  selected.has(skill.id) || selected.has(buildSkillKey(skill))

const stripProviderPrefix = (label: string, providerLabel: string): string => {
  const trimmed = label.trim()
  if (!providerLabel) return trimmed
  const escaped = providerLabel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const pattern = new RegExp(`^${escaped}\\s*[·:-]\\s+`, "i")
  return trimmed.replace(pattern, "")
}

export const formatProviderCell = (item: ModelMenuItem, width: number, query: string): string => {
  const currentGlyph = item.isCurrent ? `${ICONS.bullet} ` : "  "
  const defaultGlyph = item.isDefault ? ` ${STAR_GLYPH}` : ""
  const providerLabel = formatProviderLabel(item.provider)
  const displayLabel = stripProviderPrefix(item.label, providerLabel)
  const formatted = formatCell(`${currentGlyph}${displayLabel}${defaultGlyph}`, width, "left")
  return query.trim().length > 0 ? highlightFuzzyLabel(formatted, formatted, query) : formatted
}

export const formatContextCell = (contextTokens: number | null | undefined, width: number): string => {
  if (contextTokens == null) return formatCell(DASH_GLYPH, width, "right")
  const contextK = Math.max(1, Math.round(contextTokens / 1000))
  return formatCell(`${contextK}k`, width, "right")
}

export const formatPriceCell = (price: number | null | undefined, width: number): string => {
  if (price == null) return formatCell(DASH_GLYPH, width, "right")
  return formatCell(`$${price.toFixed(2)}`, width, "right")
}

export const buildModelRowText = (cells: string[]): string =>
  cells.filter((cell) => cell.length > 0).join(COLUMN_SEPARATOR)

export const centerPlain = (value: string, width: number): string => {
  if (width <= 0) return ""
  if (value.length >= width) return formatCell(value, width, "left")
  const left = Math.floor((width - value.length) / 2)
  const right = width - value.length - left
  return `${" ".repeat(left)}${value}${" ".repeat(right)}`
}

export const colorLine = (value: string, width: number, color: string): string =>
  CHALK.hex(color)(formatCell(value, width, "left"))

export const colorCentered = (value: string, width: number, color: string): string =>
  CHALK.hex(color)(centerPlain(value, width))

export const formatProfileLabel = (profile: string): string => {
  if (profile === "claude_v1") return "Claude Code"
  if (profile === "codex_v1") return "Codex"
  if (profile === "opencode_v1") return "OpenCode"
  if (profile === "breadboard_v1") return "BreadBoard"
  return profile
}

export const resolveGreetingName = (): string => {
  const raw =
    (process.env.BREADBOARD_USER_NAME ?? process.env.USER ?? process.env.LOGNAME ?? "").trim()
  if (!raw) return "there"
  const token = raw.split(/[._-]/)[0] ?? raw
  return token ? `${token[0]?.toUpperCase() ?? ""}${token.slice(1)}` : "there"
}
