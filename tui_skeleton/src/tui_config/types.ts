import type { ColorMode } from "../repl/designSystem.js"

export type TuiPresetId =
  | "breadboard_default"
  | "claude_code_like"
  | "codex_cli_like"
  | "claude_like_subagents"
  | "opencode_like_subagents"
  | "claude_like_subagents_swap"
  | "codex_like_subagents_dense"
export type LandingVariant = "auto" | "board" | "split" | "compact"
export type LandingBorderStyle = "round" | "single"
export type StatusLinePosition = "above_input" | "below_input"
export type StatusLineAlign = "left" | "right"
export type SubagentFocusMode = "lane" | "swap"

export type TuiDiffColorPaletteInput = {
  addLineBg?: string
  deleteLineBg?: string
  hunkLineBg?: string
  addInlineBg?: string
  deleteInlineBg?: string
  addText?: string
  deleteText?: string
  hunkText?: string
  metaText?: string
}

export type TuiConfigInput = {
  preset?: string
  display?: {
    asciiOnly?: boolean
    colorMode?: ColorMode
  }
  landing?: {
    variant?: LandingVariant
    borderStyle?: LandingBorderStyle
    showAsciiArt?: boolean
  }
  composer?: {
    promptPrefix?: string
    placeholderClassic?: string
    placeholderClaude?: string
    ruleCharacter?: string
    showTopRule?: boolean
    showBottomRule?: boolean
    todoPreviewAboveInput?: boolean
    todoPreviewMaxItems?: number
  }
  statusLine?: {
    position?: StatusLinePosition
    align?: StatusLineAlign
    showWhenPending?: boolean
    showOnComplete?: boolean
    activeText?: string
    completionTemplate?: string
  }
  markdown?: {
    shikiTheme?: string
  }
  diff?: {
    previewMaxLines?: number
    maxTokenizedLines?: number
    colors?: TuiDiffColorPaletteInput
  }
  subagents?: {
    enabled?: boolean
    stripEnabled?: boolean
    toastsEnabled?: boolean
    taskboardEnabled?: boolean
    focusEnabled?: boolean
    focusMode?: SubagentFocusMode
    coalesceMs?: number
    maxWorkItems?: number
    maxStepsPerTask?: number
  }
}

export type ResolvedTuiConfig = {
  readonly preset: TuiPresetId
  readonly display: {
    readonly asciiOnly: boolean
    readonly colorMode: ColorMode
  }
  readonly landing: {
    readonly variant: LandingVariant
    readonly borderStyle: LandingBorderStyle
    readonly showAsciiArt: boolean
  }
  readonly composer: {
    readonly promptPrefix: string
    readonly placeholderClassic: string
    readonly placeholderClaude: string
    readonly ruleCharacter: string
    readonly showTopRule: boolean
    readonly showBottomRule: boolean
    readonly todoPreviewAboveInput: boolean
    readonly todoPreviewMaxItems: number
  }
  readonly statusLine: {
    readonly position: StatusLinePosition
    readonly align: StatusLineAlign
    readonly showWhenPending: boolean
    readonly showOnComplete: boolean
    readonly activeText: string
    readonly completionTemplate: string
  }
  readonly markdown: {
    readonly shikiTheme: string
  }
  readonly diff: {
    readonly previewMaxLines: number
    readonly maxTokenizedLines: number
    readonly colors: {
      readonly addLineBg: string
      readonly deleteLineBg: string
      readonly hunkLineBg: string
      readonly addInlineBg: string
      readonly deleteInlineBg: string
      readonly addText: string
      readonly deleteText: string
      readonly hunkText: string
      readonly metaText: string
    }
  }
  readonly subagents: {
    readonly enabled: boolean
    readonly stripEnabled: boolean
    readonly toastsEnabled: boolean
    readonly taskboardEnabled: boolean
    readonly focusEnabled: boolean
    readonly focusMode: SubagentFocusMode
    readonly coalesceMs: number
    readonly maxWorkItems: number
    readonly maxStepsPerTask: number
  }
  readonly meta: {
    readonly strict: boolean
    readonly warnings: readonly string[]
    readonly sources: readonly string[]
  }
}

export type ResolvedTuiConfigOptions = {
  readonly workspace?: string | null
  readonly cliPreset?: string | null
  readonly cliConfigPath?: string | null
  readonly cliStrict?: boolean | null
  readonly colorAllowed?: boolean
}
