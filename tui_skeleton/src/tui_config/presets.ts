import type { ResolvedTuiConfig, TuiConfigInput, TuiPresetId } from "./types.js"

export const BUILTIN_TUI_PRESETS: Record<TuiPresetId, TuiConfigInput> = {
  breadboard_default: {
    landing: {
      variant: "auto",
      borderStyle: "round",
      showAsciiArt: true,
    },
    composer: {
      promptPrefix: "❯",
      placeholderClassic: "Type your request…",
      placeholderClaude: 'Try "refactor <filepath>"',
      ruleCharacter: "─",
      showTopRule: true,
      showBottomRule: true,
    },
    statusLine: {
      position: "above_input",
      align: "left",
      showWhenPending: true,
      showOnComplete: true,
      activeText: "· Deciphering… (esc to interrupt · thinking)",
      completionTemplate: "✻ Cooked for {duration}",
    },
  },
  claude_code_like: {
    landing: {
      variant: "board",
      borderStyle: "round",
      showAsciiArt: true,
    },
    composer: {
      promptPrefix: "❯",
      placeholderClassic: "Type your request…",
      placeholderClaude: 'Try "fix typecheck errors"',
      ruleCharacter: "─",
      showTopRule: true,
      showBottomRule: true,
    },
    statusLine: {
      position: "above_input",
      align: "left",
      showWhenPending: true,
      showOnComplete: true,
      activeText: "· Deciphering… (esc to interrupt · thinking)",
      completionTemplate: "✻ Cooked for {duration}",
    },
  },
  codex_cli_like: {
    landing: {
      variant: "compact",
      borderStyle: "single",
      showAsciiArt: true,
    },
    composer: {
      promptPrefix: "›",
      placeholderClassic: "Write tests for @filename",
      placeholderClaude: "Find and fix a bug in @filename",
      ruleCharacter: "─",
      showTopRule: true,
      showBottomRule: false,
    },
    statusLine: {
      position: "above_input",
      align: "left",
      showWhenPending: true,
      showOnComplete: true,
      activeText: "• Working ({elapsed} • esc to interrupt)",
      completionTemplate: "• Worked for {duration}",
    },
  },
}

export const DEFAULT_TUI_PRESET: TuiPresetId = "breadboard_default"

export const DEFAULT_RESOLVED_TUI_CONFIG: ResolvedTuiConfig = {
  preset: DEFAULT_TUI_PRESET,
  display: {
    asciiOnly: false,
    colorMode: "truecolor",
  },
  landing: {
    variant: "auto",
    borderStyle: "round",
    showAsciiArt: true,
  },
  composer: {
    promptPrefix: "❯",
    placeholderClassic: "Type your request…",
    placeholderClaude: 'Try "refactor <filepath>"',
    ruleCharacter: "─",
    showTopRule: true,
    showBottomRule: true,
  },
  statusLine: {
    position: "above_input",
    align: "left",
    showWhenPending: true,
    showOnComplete: true,
    activeText: "· Deciphering… (esc to interrupt · thinking)",
    completionTemplate: "✻ Cooked for {duration}",
  },
  markdown: {
    shikiTheme: "andromeeda",
  },
  diff: {
    previewMaxLines: 24,
    maxTokenizedLines: 400,
    colors: {
      addLineBg: "#123225",
      deleteLineBg: "#3a1426",
      hunkLineBg: "#2c2436",
      addInlineBg: "#1a4d33",
      deleteInlineBg: "#6d1c3a",
      addText: "#2dbd8d",
      deleteText: "#ff5f79",
      hunkText: "#B36BFF",
      metaText: "#7CF2FF",
    },
  },
  meta: {
    strict: false,
    warnings: [],
    sources: ["defaults"],
  },
}

export const isBuiltinPreset = (value: string): value is TuiPresetId =>
  value === "breadboard_default" || value === "claude_code_like" || value === "codex_cli_like"
