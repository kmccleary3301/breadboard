import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { parse } from "yaml"
import { resolveAsciiOnly, resolveColorMode } from "../repl/designSystem.js"
import { BUILTIN_TUI_PRESETS, DEFAULT_RESOLVED_TUI_CONFIG, DEFAULT_TUI_PRESET, isBuiltinPreset } from "./presets.js"
import { formatValidationIssues, validateTuiConfigInput } from "./schema.js"
import type { ResolvedTuiConfig, ResolvedTuiConfigOptions, TuiConfigInput, TuiPresetId } from "./types.js"

const BOOL_TRUE = new Set(["1", "true", "yes", "on"])
const BOOL_FALSE = new Set(["0", "false", "no", "off"])

const parseBooleanLike = (value: unknown): boolean | null => {
  if (typeof value === "boolean") return value
  if (typeof value !== "string") return null
  const normalized = value.trim().toLowerCase()
  if (BOOL_TRUE.has(normalized)) return true
  if (BOOL_FALSE.has(normalized)) return false
  return null
}

const mergeConfigInput = (base: TuiConfigInput, patch: TuiConfigInput): TuiConfigInput => ({
  ...base,
  ...patch,
  display: { ...(base.display ?? {}), ...(patch.display ?? {}) },
  landing: { ...(base.landing ?? {}), ...(patch.landing ?? {}) },
  composer: { ...(base.composer ?? {}), ...(patch.composer ?? {}) },
  statusLine: { ...(base.statusLine ?? {}), ...(patch.statusLine ?? {}) },
  markdown: { ...(base.markdown ?? {}), ...(patch.markdown ?? {}) },
  diff: {
    ...(base.diff ?? {}),
    ...(patch.diff ?? {}),
    colors: {
      ...(base.diff?.colors ?? {}),
      ...(patch.diff?.colors ?? {}),
    },
  },
  subagents: { ...(base.subagents ?? {}), ...(patch.subagents ?? {}) },
})

const readYamlInput = async (filePath: string, strictUnknownKeys: boolean): Promise<{ config: TuiConfigInput; warnings: string[] }> => {
  let raw: string
  try {
    raw = await fs.readFile(filePath, "utf8")
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { config: {}, warnings: [] }
    }
    throw error
  }
  const parsed = parse(raw)
  const validated = validateTuiConfigInput(parsed, { strictUnknownKeys })
  const errors = validated.issues.filter((issue) => issue.severity === "error")
  if (errors.length > 0) {
    const formatted = formatValidationIssues(errors).join("\n")
    throw new Error(`Invalid TUI config at ${filePath}\n${formatted}`)
  }
  const warnings = formatValidationIssues(validated.issues.filter((issue) => issue.severity === "warning"))
  return { config: validated.config, warnings }
}

const resolveRepoConfigPath = async (workspace?: string | null): Promise<string | null> => {
  const root = workspace?.trim() || process.cwd()
  const candidates = [path.join(root, ".breadboard", "tui.yaml"), path.join(root, "breadboard.tui.yaml")]
  for (const candidate of candidates) {
    try {
      // eslint-disable-next-line no-await-in-loop
      await fs.access(candidate)
      return candidate
    } catch {
      // Keep searching.
    }
  }
  return null
}

const resolveUserConfigPath = (): string => path.join(os.homedir(), ".config", "breadboard", "tui.yaml")

const envConfigLayer = (): TuiConfigInput => {
  const display: NonNullable<TuiConfigInput["display"]> = {}
  const landing: NonNullable<TuiConfigInput["landing"]> = {}
  const composer: NonNullable<TuiConfigInput["composer"]> = {}
  const statusLine: NonNullable<TuiConfigInput["statusLine"]> = {}
  const markdown: NonNullable<TuiConfigInput["markdown"]> = {}
  const diff: NonNullable<TuiConfigInput["diff"]> = {}
  const diffColors: NonNullable<NonNullable<TuiConfigInput["diff"]>["colors"]> = {}
  const subagents: NonNullable<TuiConfigInput["subagents"]> = {}
  const config: TuiConfigInput = {}

  if (process.env.BREADBOARD_TUI_PRESET?.trim()) config.preset = process.env.BREADBOARD_TUI_PRESET.trim()
  if (process.env.BREADBOARD_TUI_LANDING_VARIANT?.trim()) landing.variant = process.env.BREADBOARD_TUI_LANDING_VARIANT.trim() as any
  if (process.env.BREADBOARD_TUI_LANDING_BORDER_STYLE?.trim()) {
    landing.borderStyle = process.env.BREADBOARD_TUI_LANDING_BORDER_STYLE.trim() as any
  }
  const envAsciiArt = parseBooleanLike(process.env.BREADBOARD_TUI_LANDING_ASCII_ART)
  if (envAsciiArt != null) landing.showAsciiArt = envAsciiArt
  if (process.env.BREADBOARD_TUI_PROMPT_PREFIX?.trim()) composer.promptPrefix = process.env.BREADBOARD_TUI_PROMPT_PREFIX
  if (process.env.BREADBOARD_TUI_INPUT_PLACEHOLDER?.trim()) {
    composer.placeholderClassic = process.env.BREADBOARD_TUI_INPUT_PLACEHOLDER
  }
  if (process.env.BREADBOARD_TUI_CLAUDE_PLACEHOLDER?.trim()) {
    composer.placeholderClaude = process.env.BREADBOARD_TUI_CLAUDE_PLACEHOLDER
  }
  if (process.env.BREADBOARD_TUI_INPUT_RULE_CHAR?.trim()) {
    composer.ruleCharacter = process.env.BREADBOARD_TUI_INPUT_RULE_CHAR
  }
  if (process.env.BREADBOARD_TUI_STATUS_POSITION?.trim()) statusLine.position = process.env.BREADBOARD_TUI_STATUS_POSITION as any
  if (process.env.BREADBOARD_TUI_STATUS_ALIGN?.trim()) statusLine.align = process.env.BREADBOARD_TUI_STATUS_ALIGN as any
  const envStatusPending = parseBooleanLike(process.env.BREADBOARD_TUI_STATUS_PENDING)
  if (envStatusPending != null) statusLine.showWhenPending = envStatusPending
  const envStatusComplete = parseBooleanLike(process.env.BREADBOARD_TUI_STATUS_COMPLETE)
  if (envStatusComplete != null) statusLine.showOnComplete = envStatusComplete
  if (process.env.BREADBOARD_TUI_STATUS_ACTIVE_TEXT?.trim()) statusLine.activeText = process.env.BREADBOARD_TUI_STATUS_ACTIVE_TEXT
  if (process.env.BREADBOARD_TUI_STATUS_COMPLETION_TEMPLATE?.trim()) {
    statusLine.completionTemplate = process.env.BREADBOARD_TUI_STATUS_COMPLETION_TEMPLATE
  }
  if (process.env.BREADBOARD_TUI_COLOR_MODE?.trim()) display.colorMode = process.env.BREADBOARD_TUI_COLOR_MODE as any
  const envAsciiOnly = parseBooleanLike(process.env.BREADBOARD_TUI_ASCII_ONLY)
  if (envAsciiOnly != null) display.asciiOnly = envAsciiOnly
  if (process.env.BREADBOARD_TUI_SHIKI_THEME?.trim()) markdown.shikiTheme = process.env.BREADBOARD_TUI_SHIKI_THEME
  if (process.env.BREADBOARD_TUI_DIFF_PREVIEW_MAX_LINES?.trim()) {
    const parsed = Number.parseInt(process.env.BREADBOARD_TUI_DIFF_PREVIEW_MAX_LINES, 10)
    if (Number.isFinite(parsed) && parsed > 0) diff.previewMaxLines = parsed
  }
  if (process.env.BREADBOARD_TUI_DIFF_MAX_TOKENIZED_LINES?.trim()) {
    const parsed = Number.parseInt(process.env.BREADBOARD_TUI_DIFF_MAX_TOKENIZED_LINES, 10)
    if (Number.isFinite(parsed) && parsed > 0) diff.maxTokenizedLines = parsed
  }
  if (process.env.BREADBOARD_TUI_DIFF_ADD_LINE_BG?.trim()) diffColors.addLineBg = process.env.BREADBOARD_TUI_DIFF_ADD_LINE_BG
  if (process.env.BREADBOARD_TUI_DIFF_DELETE_LINE_BG?.trim()) {
    diffColors.deleteLineBg = process.env.BREADBOARD_TUI_DIFF_DELETE_LINE_BG
  }
  if (process.env.BREADBOARD_TUI_DIFF_HUNK_LINE_BG?.trim()) diffColors.hunkLineBg = process.env.BREADBOARD_TUI_DIFF_HUNK_LINE_BG
  if (process.env.BREADBOARD_TUI_DIFF_ADD_INLINE_BG?.trim()) {
    diffColors.addInlineBg = process.env.BREADBOARD_TUI_DIFF_ADD_INLINE_BG
  }
  if (process.env.BREADBOARD_TUI_DIFF_DELETE_INLINE_BG?.trim()) {
    diffColors.deleteInlineBg = process.env.BREADBOARD_TUI_DIFF_DELETE_INLINE_BG
  }
  if (process.env.BREADBOARD_TUI_DIFF_ADD_TEXT?.trim()) diffColors.addText = process.env.BREADBOARD_TUI_DIFF_ADD_TEXT
  if (process.env.BREADBOARD_TUI_DIFF_DELETE_TEXT?.trim()) diffColors.deleteText = process.env.BREADBOARD_TUI_DIFF_DELETE_TEXT
  if (process.env.BREADBOARD_TUI_DIFF_HUNK_TEXT?.trim()) diffColors.hunkText = process.env.BREADBOARD_TUI_DIFF_HUNK_TEXT
  if (process.env.BREADBOARD_TUI_DIFF_META_TEXT?.trim()) diffColors.metaText = process.env.BREADBOARD_TUI_DIFF_META_TEXT
  const envSubagentsEnabled = parseBooleanLike(process.env.BREADBOARD_TUI_SUBAGENTS_ENABLED)
  if (envSubagentsEnabled != null) subagents.enabled = envSubagentsEnabled
  const envSubagentsStrip = parseBooleanLike(process.env.BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED)
  if (envSubagentsStrip != null) subagents.stripEnabled = envSubagentsStrip
  const envSubagentsToasts = parseBooleanLike(process.env.BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED)
  if (envSubagentsToasts != null) subagents.toastsEnabled = envSubagentsToasts
  const envSubagentsTaskboard = parseBooleanLike(process.env.BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED)
  if (envSubagentsTaskboard != null) subagents.taskboardEnabled = envSubagentsTaskboard
  const envSubagentsFocus = parseBooleanLike(process.env.BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED)
  if (envSubagentsFocus != null) subagents.focusEnabled = envSubagentsFocus
  if (process.env.BREADBOARD_TUI_SUBAGENTS_FOCUS_MODE?.trim()) {
    subagents.focusMode = process.env.BREADBOARD_TUI_SUBAGENTS_FOCUS_MODE.trim() as any
  }
  if (process.env.BREADBOARD_TUI_SUBAGENTS_COALESCE_MS?.trim()) {
    const parsed = Number.parseInt(process.env.BREADBOARD_TUI_SUBAGENTS_COALESCE_MS, 10)
    if (Number.isFinite(parsed) && parsed >= 0) subagents.coalesceMs = parsed
  }
  if (process.env.BREADBOARD_TUI_SUBAGENTS_MAX_WORK_ITEMS?.trim()) {
    const parsed = Number.parseInt(process.env.BREADBOARD_TUI_SUBAGENTS_MAX_WORK_ITEMS, 10)
    if (Number.isFinite(parsed) && parsed > 0) subagents.maxWorkItems = parsed
  }
  if (process.env.BREADBOARD_TUI_SUBAGENTS_MAX_STEPS_PER_TASK?.trim()) {
    const parsed = Number.parseInt(process.env.BREADBOARD_TUI_SUBAGENTS_MAX_STEPS_PER_TASK, 10)
    if (Number.isFinite(parsed) && parsed > 0) subagents.maxStepsPerTask = parsed
  }

  if (Object.keys(display).length > 0) config.display = display
  if (Object.keys(landing).length > 0) config.landing = landing
  if (Object.keys(composer).length > 0) config.composer = composer
  if (Object.keys(statusLine).length > 0) config.statusLine = statusLine
  if (Object.keys(markdown).length > 0) config.markdown = markdown
  if (Object.keys(diffColors).length > 0) diff.colors = diffColors
  if (Object.keys(diff).length > 0) config.diff = diff
  if (Object.keys(subagents).length > 0) config.subagents = subagents
  return config
}

const normalizeRuleCharacter = (value: string | undefined, asciiOnly: boolean): string => {
  const fallback = asciiOnly ? "-" : "─"
  if (!value) return fallback
  const normalized = value.trim()
  return normalized.length > 0 ? normalized[0] : fallback
}

const sanitizePromptPrefix = (value: string | undefined, asciiOnly: boolean): string => {
  if (!value || !value.trim()) return asciiOnly ? ">" : "❯"
  return value.trim()
}

const renderTemplate = (template: string, duration: string): string => template.replaceAll("{duration}", duration)

export const formatConfiguredCompletionLine = (config: ResolvedTuiConfig, duration: string): string =>
  renderTemplate(config.statusLine.completionTemplate, duration)

export const resolveTuiConfig = async (options: ResolvedTuiConfigOptions): Promise<ResolvedTuiConfig> => {
  const strictFromEnv = parseBooleanLike(process.env.BREADBOARD_TUI_CONFIG_STRICT)
  const strict = options.cliStrict ?? strictFromEnv ?? false
  const warnings: string[] = []
  const sources: string[] = ["defaults"]
  let merged: TuiConfigInput = {}
  let selectedPreset: TuiPresetId = DEFAULT_TUI_PRESET

  const applyLayer = (layer: TuiConfigInput, source: string) => {
    if (!layer || Object.keys(layer).length === 0) return
    if (layer.preset) {
      if (!isBuiltinPreset(layer.preset)) {
        throw new Error(`Unknown TUI preset "${layer.preset}" in ${source}.`)
      }
      selectedPreset = layer.preset
      merged = mergeConfigInput(merged, BUILTIN_TUI_PRESETS[selectedPreset] ?? {})
      sources.push(`preset:${selectedPreset}`)
    }
    merged = mergeConfigInput(merged, layer)
    sources.push(source)
  }

  const initialPreset = options.cliPreset?.trim() || process.env.BREADBOARD_TUI_PRESET?.trim() || DEFAULT_TUI_PRESET
  if (!isBuiltinPreset(initialPreset)) {
    throw new Error(`Unknown TUI preset "${initialPreset}".`)
  }
  applyLayer({ preset: initialPreset }, "preset-select")

  const repoConfigPath = await resolveRepoConfigPath(options.workspace)
  if (repoConfigPath) {
    const repoLayer = await readYamlInput(repoConfigPath, strict)
    warnings.push(...repoLayer.warnings.map((line) => `${repoConfigPath}: ${line}`))
    applyLayer(repoLayer.config, `repo:${repoConfigPath}`)
  }

  const userConfigPath = resolveUserConfigPath()
  const userLayer = await readYamlInput(userConfigPath, strict)
  warnings.push(...userLayer.warnings.map((line) => `${userConfigPath}: ${line}`))
  applyLayer(userLayer.config, `user:${userConfigPath}`)

  const cliConfigPath = options.cliConfigPath?.trim()
  if (cliConfigPath) {
    const resolvedCliPath = path.isAbsolute(cliConfigPath) ? cliConfigPath : path.resolve(process.cwd(), cliConfigPath)
    const cliLayer = await readYamlInput(resolvedCliPath, strict)
    warnings.push(...cliLayer.warnings.map((line) => `${resolvedCliPath}: ${line}`))
    applyLayer(cliLayer.config, `cli-config:${resolvedCliPath}`)
  }

  applyLayer(envConfigLayer(), "env")

  const validated = validateTuiConfigInput(merged as unknown, { strictUnknownKeys: strict })
  const errors = validated.issues.filter((issue) => issue.severity === "error")
  if (errors.length > 0) {
    throw new Error(`Invalid resolved TUI config\n${formatValidationIssues(errors).join("\n")}`)
  }
  warnings.push(...formatValidationIssues(validated.issues.filter((issue) => issue.severity === "warning")))
  merged = validated.config

  const colorAllowed = options.colorAllowed ?? true
  const noColorRequested = Boolean(process.env.NO_COLOR)
  const displayAsciiOnly = merged.display?.asciiOnly ?? resolveAsciiOnly()
  // Contract: NO_COLOR must win unless the caller explicitly disables color globally
  // via `colorAllowed=false` (used by deterministic harness lanes).
  const requestedColorMode = merged.display?.colorMode ?? resolveColorMode(undefined, colorAllowed)
  const displayColorMode = colorAllowed && noColorRequested ? "none" : requestedColorMode
  const activeText = merged.statusLine?.activeText?.trim() || DEFAULT_RESOLVED_TUI_CONFIG.statusLine.activeText
  const completionTemplate =
    merged.statusLine?.completionTemplate?.trim() || DEFAULT_RESOLVED_TUI_CONFIG.statusLine.completionTemplate

  return {
    preset: selectedPreset,
    display: {
      asciiOnly: displayAsciiOnly,
      colorMode: displayColorMode,
    },
    landing: {
      variant: merged.landing?.variant ?? DEFAULT_RESOLVED_TUI_CONFIG.landing.variant,
      borderStyle: merged.landing?.borderStyle ?? DEFAULT_RESOLVED_TUI_CONFIG.landing.borderStyle,
      showAsciiArt: merged.landing?.showAsciiArt ?? DEFAULT_RESOLVED_TUI_CONFIG.landing.showAsciiArt,
    },
    composer: {
      promptPrefix: sanitizePromptPrefix(merged.composer?.promptPrefix, displayAsciiOnly),
      placeholderClassic: merged.composer?.placeholderClassic ?? DEFAULT_RESOLVED_TUI_CONFIG.composer.placeholderClassic,
      placeholderClaude: merged.composer?.placeholderClaude ?? DEFAULT_RESOLVED_TUI_CONFIG.composer.placeholderClaude,
      ruleCharacter: normalizeRuleCharacter(merged.composer?.ruleCharacter, displayAsciiOnly),
      showTopRule: merged.composer?.showTopRule ?? DEFAULT_RESOLVED_TUI_CONFIG.composer.showTopRule,
      showBottomRule: merged.composer?.showBottomRule ?? DEFAULT_RESOLVED_TUI_CONFIG.composer.showBottomRule,
    },
    statusLine: {
      position: merged.statusLine?.position ?? DEFAULT_RESOLVED_TUI_CONFIG.statusLine.position,
      align: merged.statusLine?.align ?? DEFAULT_RESOLVED_TUI_CONFIG.statusLine.align,
      showWhenPending: merged.statusLine?.showWhenPending ?? DEFAULT_RESOLVED_TUI_CONFIG.statusLine.showWhenPending,
      showOnComplete: merged.statusLine?.showOnComplete ?? DEFAULT_RESOLVED_TUI_CONFIG.statusLine.showOnComplete,
      activeText,
      completionTemplate,
    },
    markdown: {
      shikiTheme: merged.markdown?.shikiTheme?.trim() || DEFAULT_RESOLVED_TUI_CONFIG.markdown.shikiTheme,
    },
    diff: {
      previewMaxLines: merged.diff?.previewMaxLines ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.previewMaxLines,
      maxTokenizedLines: merged.diff?.maxTokenizedLines ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.maxTokenizedLines,
      colors: {
        addLineBg: merged.diff?.colors?.addLineBg ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.addLineBg,
        deleteLineBg: merged.diff?.colors?.deleteLineBg ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.deleteLineBg,
        hunkLineBg: merged.diff?.colors?.hunkLineBg ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.hunkLineBg,
        addInlineBg: merged.diff?.colors?.addInlineBg ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.addInlineBg,
        deleteInlineBg: merged.diff?.colors?.deleteInlineBg ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.deleteInlineBg,
        addText: merged.diff?.colors?.addText ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.addText,
        deleteText: merged.diff?.colors?.deleteText ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.deleteText,
        hunkText: merged.diff?.colors?.hunkText ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.hunkText,
        metaText: merged.diff?.colors?.metaText ?? DEFAULT_RESOLVED_TUI_CONFIG.diff.colors.metaText,
      },
    },
    subagents: {
      enabled: merged.subagents?.enabled ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.enabled,
      stripEnabled: merged.subagents?.stripEnabled ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.stripEnabled,
      toastsEnabled: merged.subagents?.toastsEnabled ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.toastsEnabled,
      taskboardEnabled: merged.subagents?.taskboardEnabled ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.taskboardEnabled,
      focusEnabled: merged.subagents?.focusEnabled ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.focusEnabled,
      focusMode: merged.subagents?.focusMode ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.focusMode,
      coalesceMs: merged.subagents?.coalesceMs ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.coalesceMs,
      maxWorkItems: merged.subagents?.maxWorkItems ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.maxWorkItems,
      maxStepsPerTask: merged.subagents?.maxStepsPerTask ?? DEFAULT_RESOLVED_TUI_CONFIG.subagents.maxStepsPerTask,
    },
    meta: {
      strict,
      warnings,
      sources,
    },
  }
}
