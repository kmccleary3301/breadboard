import type { TuiConfigInput } from "./types.js"

type ValidationIssue = {
  readonly severity: "error" | "warning"
  readonly path: string
  readonly message: string
}

type ValidationResult = {
  readonly config: TuiConfigInput
  readonly issues: readonly ValidationIssue[]
}

type ValidationOptions = {
  readonly strictUnknownKeys: boolean
}

const BOOL_TRUE = new Set(["1", "true", "yes", "on"])
const BOOL_FALSE = new Set(["0", "false", "no", "off"])

const toPath = (parts: readonly string[]): string => (parts.length > 0 ? parts.join(".") : "<root>")

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value != null

const parseBooleanLike = (value: unknown): boolean | undefined => {
  if (typeof value === "boolean") return value
  if (typeof value !== "string") return undefined
  const normalized = value.trim().toLowerCase()
  if (BOOL_TRUE.has(normalized)) return true
  if (BOOL_FALSE.has(normalized)) return false
  return undefined
}

const readRecord = (
  source: Record<string, unknown>,
  key: string,
  path: readonly string[],
  issues: ValidationIssue[],
): Record<string, unknown> | undefined => {
  if (!(key in source) || source[key] == null) return undefined
  const value = source[key]
  if (!isRecord(value)) {
    issues.push({
      severity: "error",
      path: toPath([...path, key]),
      message: `Expected object, received ${typeof value}.`,
    })
    return undefined
  }
  return value
}

const readBoolean = (
  source: Record<string, unknown>,
  key: string,
  path: readonly string[],
  issues: ValidationIssue[],
): boolean | undefined => {
  if (!(key in source) || source[key] == null) return undefined
  const parsed = parseBooleanLike(source[key])
  if (parsed == null) {
    issues.push({
      severity: "error",
      path: toPath([...path, key]),
      message: "Expected boolean (or bool-like string).",
    })
    return undefined
  }
  return parsed
}

const readString = (
  source: Record<string, unknown>,
  key: string,
  path: readonly string[],
  issues: ValidationIssue[],
): string | undefined => {
  if (!(key in source) || source[key] == null) return undefined
  const value = source[key]
  if (typeof value !== "string") {
    issues.push({
      severity: "error",
      path: toPath([...path, key]),
      message: `Expected string, received ${typeof value}.`,
    })
    return undefined
  }
  return value
}

const readPositiveInt = (
  source: Record<string, unknown>,
  key: string,
  path: readonly string[],
  issues: ValidationIssue[],
): number | undefined => {
  if (!(key in source) || source[key] == null) return undefined
  const value = source[key]
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number.parseInt(value.trim(), 10)
        : Number.NaN
  if (!Number.isFinite(parsed) || parsed < 1 || !Number.isInteger(parsed)) {
    issues.push({
      severity: "error",
      path: toPath([...path, key]),
      message: "Expected positive integer.",
    })
    return undefined
  }
  return parsed
}

const readNonNegativeInt = (
  source: Record<string, unknown>,
  key: string,
  path: readonly string[],
  issues: ValidationIssue[],
): number | undefined => {
  if (!(key in source) || source[key] == null) return undefined
  const value = source[key]
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number.parseInt(value.trim(), 10)
        : Number.NaN
  if (!Number.isFinite(parsed) || parsed < 0 || !Number.isInteger(parsed)) {
    issues.push({
      severity: "error",
      path: toPath([...path, key]),
      message: "Expected non-negative integer.",
    })
    return undefined
  }
  return parsed
}

const readEnum = <T extends string>(
  source: Record<string, unknown>,
  key: string,
  values: readonly T[],
  path: readonly string[],
  issues: ValidationIssue[],
): T | undefined => {
  const raw = readString(source, key, path, issues)
  if (raw == null) return undefined
  if (!values.includes(raw as T)) {
    issues.push({
      severity: "error",
      path: toPath([...path, key]),
      message: `Expected one of ${values.join(", ")}.`,
    })
    return undefined
  }
  return raw as T
}

const detectUnknownKeys = (
  source: Record<string, unknown>,
  allowed: readonly string[],
  path: readonly string[],
  issues: ValidationIssue[],
  strictUnknownKeys: boolean,
) => {
  for (const key of Object.keys(source)) {
    if (allowed.includes(key)) continue
    issues.push({
      severity: strictUnknownKeys ? "error" : "warning",
      path: toPath([...path, key]),
      message: "Unknown key.",
    })
  }
}

export const validateTuiConfigInput = (input: unknown, options: ValidationOptions): ValidationResult => {
  const issues: ValidationIssue[] = []
  if (!isRecord(input)) {
    issues.push({
      severity: "error",
      path: "<root>",
      message: "Expected top-level object.",
    })
    return { config: {}, issues }
  }

  const root = input
  detectUnknownKeys(
    root,
    ["preset", "display", "landing", "composer", "statusLine", "markdown", "diff", "subagents"],
    [],
    issues,
    options.strictUnknownKeys,
  )

  const config: TuiConfigInput = {}
  const preset = readString(root, "preset", [], issues)
  if (preset != null) config.preset = preset

  const displayRaw = readRecord(root, "display", [], issues)
  if (displayRaw) {
    detectUnknownKeys(displayRaw, ["asciiOnly", "colorMode"], ["display"], issues, options.strictUnknownKeys)
    const display: NonNullable<TuiConfigInput["display"]> = {}
    const asciiOnly = readBoolean(displayRaw, "asciiOnly", ["display"], issues)
    if (asciiOnly != null) display.asciiOnly = asciiOnly
    const colorMode = readEnum(
      displayRaw,
      "colorMode",
      ["truecolor", "ansi256", "ansi16", "none"] as const,
      ["display"],
      issues,
    )
    if (colorMode != null) display.colorMode = colorMode
    config.display = display
  }

  const landingRaw = readRecord(root, "landing", [], issues)
  if (landingRaw) {
    detectUnknownKeys(
      landingRaw,
      ["variant", "borderStyle", "showAsciiArt"],
      ["landing"],
      issues,
      options.strictUnknownKeys,
    )
    const landing: NonNullable<TuiConfigInput["landing"]> = {}
    const variant = readEnum(
      landingRaw,
      "variant",
      ["auto", "board", "split", "compact"] as const,
      ["landing"],
      issues,
    )
    if (variant != null) landing.variant = variant
    const borderStyle = readEnum(
      landingRaw,
      "borderStyle",
      ["round", "single"] as const,
      ["landing"],
      issues,
    )
    if (borderStyle != null) landing.borderStyle = borderStyle
    const showAsciiArt = readBoolean(landingRaw, "showAsciiArt", ["landing"], issues)
    if (showAsciiArt != null) landing.showAsciiArt = showAsciiArt
    config.landing = landing
  }

  const composerRaw = readRecord(root, "composer", [], issues)
  if (composerRaw) {
    detectUnknownKeys(
      composerRaw,
      [
        "promptPrefix",
        "placeholderClassic",
        "placeholderClaude",
        "ruleCharacter",
        "showTopRule",
        "showBottomRule",
        "todoPreviewAboveInput",
        "todoPreviewMaxItems",
        "todoPreviewSelection",
        "todoPreviewShowHiddenCount",
        "todoPreviewStyle",
        "todoPreviewMinRowsToShow",
        "todoPreviewSmallRowsMaxItems",
        "todoAutoFollowScope",
        "todoAutoFollowHysteresisMs",
        "todoAutoFollowManualOverrideMs",
      ],
      ["composer"],
      issues,
      options.strictUnknownKeys,
    )
    const composer: NonNullable<TuiConfigInput["composer"]> = {}
    const promptPrefix = readString(composerRaw, "promptPrefix", ["composer"], issues)
    if (promptPrefix != null) composer.promptPrefix = promptPrefix
    const placeholderClassic = readString(composerRaw, "placeholderClassic", ["composer"], issues)
    if (placeholderClassic != null) composer.placeholderClassic = placeholderClassic
    const placeholderClaude = readString(composerRaw, "placeholderClaude", ["composer"], issues)
    if (placeholderClaude != null) composer.placeholderClaude = placeholderClaude
    const ruleCharacter = readString(composerRaw, "ruleCharacter", ["composer"], issues)
    if (ruleCharacter != null) composer.ruleCharacter = ruleCharacter
    const showTopRule = readBoolean(composerRaw, "showTopRule", ["composer"], issues)
    if (showTopRule != null) composer.showTopRule = showTopRule
    const showBottomRule = readBoolean(composerRaw, "showBottomRule", ["composer"], issues)
    if (showBottomRule != null) composer.showBottomRule = showBottomRule
    const todoPreviewAboveInput = readBoolean(composerRaw, "todoPreviewAboveInput", ["composer"], issues)
    if (todoPreviewAboveInput != null) composer.todoPreviewAboveInput = todoPreviewAboveInput
    const todoPreviewMaxItems = readPositiveInt(composerRaw, "todoPreviewMaxItems", ["composer"], issues)
    if (todoPreviewMaxItems != null) composer.todoPreviewMaxItems = todoPreviewMaxItems
    const todoPreviewSelection = readEnum(
      composerRaw,
      "todoPreviewSelection",
      ["first_n", "incomplete_first", "active_first"] as const,
      ["composer"],
      issues,
    )
    if (todoPreviewSelection != null) composer.todoPreviewSelection = todoPreviewSelection
    const todoPreviewShowHiddenCount = readBoolean(composerRaw, "todoPreviewShowHiddenCount", ["composer"], issues)
    if (todoPreviewShowHiddenCount != null) composer.todoPreviewShowHiddenCount = todoPreviewShowHiddenCount
    const todoPreviewStyle = readEnum(
      composerRaw,
      "todoPreviewStyle",
      ["minimal", "nice", "dense"] as const,
      ["composer"],
      issues,
    )
    if (todoPreviewStyle != null) composer.todoPreviewStyle = todoPreviewStyle
    const todoPreviewMinRowsToShow = readPositiveInt(composerRaw, "todoPreviewMinRowsToShow", ["composer"], issues)
    if (todoPreviewMinRowsToShow != null) composer.todoPreviewMinRowsToShow = todoPreviewMinRowsToShow
    const todoPreviewSmallRowsMaxItems = readPositiveInt(composerRaw, "todoPreviewSmallRowsMaxItems", ["composer"], issues)
    if (todoPreviewSmallRowsMaxItems != null) composer.todoPreviewSmallRowsMaxItems = todoPreviewSmallRowsMaxItems
    const todoAutoFollowScope = readEnum(composerRaw, "todoAutoFollowScope", ["off", "on"] as const, ["composer"], issues)
    if (todoAutoFollowScope != null) composer.todoAutoFollowScope = todoAutoFollowScope
    const todoAutoFollowHysteresisMs = readNonNegativeInt(composerRaw, "todoAutoFollowHysteresisMs", ["composer"], issues)
    if (todoAutoFollowHysteresisMs != null) composer.todoAutoFollowHysteresisMs = todoAutoFollowHysteresisMs
    const todoAutoFollowManualOverrideMs = readNonNegativeInt(composerRaw, "todoAutoFollowManualOverrideMs", ["composer"], issues)
    if (todoAutoFollowManualOverrideMs != null) composer.todoAutoFollowManualOverrideMs = todoAutoFollowManualOverrideMs
    config.composer = composer
  }

  const statusRaw = readRecord(root, "statusLine", [], issues)
  if (statusRaw) {
    detectUnknownKeys(
      statusRaw,
      ["position", "align", "showWhenPending", "showOnComplete", "activeText", "completionTemplate"],
      ["statusLine"],
      issues,
      options.strictUnknownKeys,
    )
    const statusLine: NonNullable<TuiConfigInput["statusLine"]> = {}
    const position = readEnum(
      statusRaw,
      "position",
      ["above_input", "below_input"] as const,
      ["statusLine"],
      issues,
    )
    if (position != null) statusLine.position = position
    const align = readEnum(
      statusRaw,
      "align",
      ["left", "right"] as const,
      ["statusLine"],
      issues,
    )
    if (align != null) statusLine.align = align
    const showWhenPending = readBoolean(statusRaw, "showWhenPending", ["statusLine"], issues)
    if (showWhenPending != null) statusLine.showWhenPending = showWhenPending
    const showOnComplete = readBoolean(statusRaw, "showOnComplete", ["statusLine"], issues)
    if (showOnComplete != null) statusLine.showOnComplete = showOnComplete
    const activeText = readString(statusRaw, "activeText", ["statusLine"], issues)
    if (activeText != null) statusLine.activeText = activeText
    const completionTemplate = readString(statusRaw, "completionTemplate", ["statusLine"], issues)
    if (completionTemplate != null) statusLine.completionTemplate = completionTemplate
    config.statusLine = statusLine
  }

  const markdownRaw = readRecord(root, "markdown", [], issues)
  if (markdownRaw) {
    detectUnknownKeys(markdownRaw, ["shikiTheme"], ["markdown"], issues, options.strictUnknownKeys)
    const markdown: NonNullable<TuiConfigInput["markdown"]> = {}
    const shikiTheme = readString(markdownRaw, "shikiTheme", ["markdown"], issues)
    if (shikiTheme != null) markdown.shikiTheme = shikiTheme
    config.markdown = markdown
  }

  const diffRaw = readRecord(root, "diff", [], issues)
  if (diffRaw) {
    detectUnknownKeys(
      diffRaw,
      ["previewMaxLines", "maxTokenizedLines", "colors"],
      ["diff"],
      issues,
      options.strictUnknownKeys,
    )
    const diff: NonNullable<TuiConfigInput["diff"]> = {}
    const previewMaxLines = readPositiveInt(diffRaw, "previewMaxLines", ["diff"], issues)
    if (previewMaxLines != null) diff.previewMaxLines = previewMaxLines
    const maxTokenizedLines = readPositiveInt(diffRaw, "maxTokenizedLines", ["diff"], issues)
    if (maxTokenizedLines != null) diff.maxTokenizedLines = maxTokenizedLines
    const colorsRaw = readRecord(diffRaw, "colors", ["diff"], issues)
    if (colorsRaw) {
      detectUnknownKeys(
        colorsRaw,
        [
          "addLineBg",
          "deleteLineBg",
          "hunkLineBg",
          "addInlineBg",
          "deleteInlineBg",
          "addText",
          "deleteText",
          "hunkText",
          "metaText",
        ],
        ["diff", "colors"],
        issues,
        options.strictUnknownKeys,
      )
      const colors: NonNullable<NonNullable<TuiConfigInput["diff"]>["colors"]> = {}
      const addLineBg = readString(colorsRaw, "addLineBg", ["diff", "colors"], issues)
      if (addLineBg != null) colors.addLineBg = addLineBg
      const deleteLineBg = readString(colorsRaw, "deleteLineBg", ["diff", "colors"], issues)
      if (deleteLineBg != null) colors.deleteLineBg = deleteLineBg
      const hunkLineBg = readString(colorsRaw, "hunkLineBg", ["diff", "colors"], issues)
      if (hunkLineBg != null) colors.hunkLineBg = hunkLineBg
      const addInlineBg = readString(colorsRaw, "addInlineBg", ["diff", "colors"], issues)
      if (addInlineBg != null) colors.addInlineBg = addInlineBg
      const deleteInlineBg = readString(colorsRaw, "deleteInlineBg", ["diff", "colors"], issues)
      if (deleteInlineBg != null) colors.deleteInlineBg = deleteInlineBg
      const addText = readString(colorsRaw, "addText", ["diff", "colors"], issues)
      if (addText != null) colors.addText = addText
      const deleteText = readString(colorsRaw, "deleteText", ["diff", "colors"], issues)
      if (deleteText != null) colors.deleteText = deleteText
      const hunkText = readString(colorsRaw, "hunkText", ["diff", "colors"], issues)
      if (hunkText != null) colors.hunkText = hunkText
      const metaText = readString(colorsRaw, "metaText", ["diff", "colors"], issues)
      if (metaText != null) colors.metaText = metaText
      diff.colors = colors
    }
    config.diff = diff
  }

  const subagentsRaw = readRecord(root, "subagents", [], issues)
  if (subagentsRaw) {
    detectUnknownKeys(
      subagentsRaw,
      [
        "enabled",
        "stripEnabled",
        "toastsEnabled",
        "taskboardEnabled",
        "focusEnabled",
        "focusMode",
        "coalesceMs",
        "maxWorkItems",
        "maxStepsPerTask",
      ],
      ["subagents"],
      issues,
      options.strictUnknownKeys,
    )
    const subagents: NonNullable<TuiConfigInput["subagents"]> = {}
    const enabled = readBoolean(subagentsRaw, "enabled", ["subagents"], issues)
    if (enabled != null) subagents.enabled = enabled
    const stripEnabled = readBoolean(subagentsRaw, "stripEnabled", ["subagents"], issues)
    if (stripEnabled != null) subagents.stripEnabled = stripEnabled
    const toastsEnabled = readBoolean(subagentsRaw, "toastsEnabled", ["subagents"], issues)
    if (toastsEnabled != null) subagents.toastsEnabled = toastsEnabled
    const taskboardEnabled = readBoolean(subagentsRaw, "taskboardEnabled", ["subagents"], issues)
    if (taskboardEnabled != null) subagents.taskboardEnabled = taskboardEnabled
    const focusEnabled = readBoolean(subagentsRaw, "focusEnabled", ["subagents"], issues)
    if (focusEnabled != null) subagents.focusEnabled = focusEnabled
    const focusMode = readEnum(subagentsRaw, "focusMode", ["lane", "swap"] as const, ["subagents"], issues)
    if (focusMode != null) subagents.focusMode = focusMode
    const coalesceMs = readNonNegativeInt(subagentsRaw, "coalesceMs", ["subagents"], issues)
    if (coalesceMs != null) subagents.coalesceMs = coalesceMs
    const maxWorkItems = readPositiveInt(subagentsRaw, "maxWorkItems", ["subagents"], issues)
    if (maxWorkItems != null) subagents.maxWorkItems = maxWorkItems
    const maxStepsPerTask = readPositiveInt(subagentsRaw, "maxStepsPerTask", ["subagents"], issues)
    if (maxStepsPerTask != null) subagents.maxStepsPerTask = maxStepsPerTask
    config.subagents = subagents
  }

  return { config, issues }
}

export const formatValidationIssues = (issues: readonly ValidationIssue[]): string[] =>
  issues.map((issue) => `[${issue.severity}] ${issue.path}: ${issue.message}`)
