export const DESIGN_SYSTEM_VERSION = "1.0.0"

const resolveTerminalBg = () => {
  const raw = (process.env.BREADBOARD_TERMINAL_BG ?? "").trim()
  return raw || "#1f2430"
}

export const TERMINAL_BG = resolveTerminalBg()

export const BRAND_COLORS = {
  jamRed: "#ff4d6d",
  magenta: "#d94dff",
  duneOrange: "#ed840f",
  jumperGreen: "#2ee59d",
  railBlue: "#4da3ff",
} as const

export const NEUTRAL_COLORS = {
  nearWhite: "#f1f5f9",
  offWhite: "#e2e8f0",
  lightGray: "#a8b0bd",
  midGray: "#7c8696",
  dimGray: "#526070",
  darkGray: "#1f2937",
  nearBlack: TERMINAL_BG,
} as const

export const SEMANTIC_COLORS = {
  user: BRAND_COLORS.railBlue,
  assistant: NEUTRAL_COLORS.offWhite,
  system: BRAND_COLORS.duneOrange,
  tool: BRAND_COLORS.duneOrange,
  toolOutput: NEUTRAL_COLORS.lightGray,
  toolOutputHint: NEUTRAL_COLORS.midGray,
  reasoning: BRAND_COLORS.railBlue,
  success: BRAND_COLORS.jumperGreen,
  error: BRAND_COLORS.jamRed,
  warning: BRAND_COLORS.duneOrange,
  info: BRAND_COLORS.railBlue,
  muted: NEUTRAL_COLORS.dimGray,
} as const

export const ICONS = {
  userChevron: ">",
  bullet: "•",
  activeAsterisk: "✱",
  completeAsterisk: "✻",
  treeBranch: "└",
  verticalLine: "│",
  ellipsis: "…",
  collapseClosed: "▸",
  collapseOpen: "▾",
  error: "✗",
  warning: "⚠",
  success: "✓",
  arrow: "→",
  dollar: "$",
} as const
