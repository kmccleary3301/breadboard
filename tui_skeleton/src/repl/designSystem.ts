export const DESIGN_SYSTEM_VERSION = "1.0.0"

export const BRAND_COLORS = {
  jamRed: "#ff4d6d",
  strawberryRed: "#ff5f8f",
  duneOrange: "#ed840f",
  jumperGreen: "#2ee59d",
  railBlue: "#4da3ff",
} as const

export const NEUTRAL_COLORS = {
  nearWhite: "#e6e6eb",
  offWhite: "#dcdce1",
  lightGray: "#b4b4be",
  midGray: "#8c8c96",
  dimGray: "#64646e",
  darkGray: "#3c3c46",
  nearBlack: "#1a1a1e",
} as const

export const SEMANTIC_COLORS = {
  user: BRAND_COLORS.railBlue,
  assistant: NEUTRAL_COLORS.offWhite,
  system: BRAND_COLORS.duneOrange,
  tool: BRAND_COLORS.duneOrange,
  success: BRAND_COLORS.jumperGreen,
  error: BRAND_COLORS.jamRed,
  warning: BRAND_COLORS.duneOrange,
  info: BRAND_COLORS.railBlue,
  muted: NEUTRAL_COLORS.dimGray,
} as const

export const ICONS = {
  userChevron: "❯",
  bullet: "●",
  activeAsterisk: "✱",
  completeAsterisk: "✻",
  treeBranch: "└",
  verticalLine: "│",
  ellipsis: "…",
  error: "✗",
  warning: "⚠",
  success: "✓",
  arrow: "→",
  dollar: "$",
} as const

export const ASCII_FALLBACK_ICONS = {
  userChevron: ">",
  bullet: "*",
  activeAsterisk: "*",
  completeAsterisk: "*",
  treeBranch: "`-",
  verticalLine: "|",
  ellipsis: "...",
  error: "X",
  warning: "!",
  success: "OK",
  arrow: "->",
  dollar: "$",
} as const

export const STATUS_VERBS = {
  active: [
    "Breadboarding",
    "Prototyping",
    "Wiring",
    "Soldering",
    "Routing",
    "Assembling",
    "Synthesizing",
    "Manifesting",
    "Forging",
    "Crafting",
    "Spelunking",
    "Excavating",
    "Prospecting",
    "Surveying",
    "Percolating",
    "Ruminating",
    "Cogitating",
    "Contemplating",
  ],
  complete: [
    "Baked",
    "Toasted",
    "Crisped",
    "Simmered",
    "Forged",
    "Assembled",
    "Wired",
    "Soldered",
    "Completed",
    "Finished",
    "Wrapped",
  ],
} as const

export const STATUS_SPINNER_FRAMES = ["·", "*", "✶", "✢", "✽"] as const

export type ColorMode = "truecolor" | "ansi256" | "ansi16" | "none"

type Ansi16ColorName =
  | "black"
  | "red"
  | "green"
  | "yellow"
  | "blue"
  | "magenta"
  | "cyan"
  | "white"
  | "gray"
  | "grey"
  | "redBright"
  | "greenBright"
  | "yellowBright"
  | "blueBright"
  | "magentaBright"
  | "cyanBright"
  | "whiteBright"
  | "blackBright"

const ANSI256_FALLBACK: Record<string, number> = {
  [BRAND_COLORS.jamRed]: 203,
  [BRAND_COLORS.strawberryRed]: 205,
  [BRAND_COLORS.duneOrange]: 208,
  [BRAND_COLORS.jumperGreen]: 48,
  [BRAND_COLORS.railBlue]: 75,
  [NEUTRAL_COLORS.nearWhite]: 254,
  [NEUTRAL_COLORS.offWhite]: 253,
  [NEUTRAL_COLORS.lightGray]: 250,
  [NEUTRAL_COLORS.midGray]: 246,
  [NEUTRAL_COLORS.dimGray]: 241,
  [NEUTRAL_COLORS.darkGray]: 238,
  [NEUTRAL_COLORS.nearBlack]: 232,
}

const ANSI16_FALLBACK: Record<string, Ansi16ColorName> = {
  [BRAND_COLORS.jamRed]: "redBright",
  [BRAND_COLORS.strawberryRed]: "magentaBright",
  [BRAND_COLORS.duneOrange]: "yellow",
  [BRAND_COLORS.jumperGreen]: "greenBright",
  [BRAND_COLORS.railBlue]: "blueBright",
  [NEUTRAL_COLORS.nearWhite]: "whiteBright",
  [NEUTRAL_COLORS.offWhite]: "white",
  [NEUTRAL_COLORS.lightGray]: "white",
  [NEUTRAL_COLORS.midGray]: "gray",
  [NEUTRAL_COLORS.dimGray]: "gray",
  [NEUTRAL_COLORS.darkGray]: "gray",
  [NEUTRAL_COLORS.nearBlack]: "black",
}

export const resolveAsciiOnly = (override?: boolean): boolean => {
  if (override != null) return override
  const raw = (process.env.BREADBOARD_ASCII ?? process.env.BREADBOARD_NO_UNICODE ?? "").toString().toLowerCase().trim()
  if (!raw) return false
  return ["1", "true", "yes", "on"].includes(raw)
}

export const resolveIcons = (asciiOnly?: boolean) =>
  resolveAsciiOnly(asciiOnly) ? ASCII_FALLBACK_ICONS : ICONS

export const resolveColorMode = (override?: ColorMode, allowColor = true): ColorMode => {
  if (!allowColor) return "none"
  if (override) return override
  if (process.env.NO_COLOR) return "none"
  const raw = (process.env.BREADBOARD_COLOR_MODE ?? "").toString().toLowerCase().trim()
  if (!raw) return "truecolor"
  if (["0", "none", "off", "false"].includes(raw)) return "none"
  if (["16", "ansi16", "basic"].includes(raw)) return "ansi16"
  if (["256", "ansi256"].includes(raw)) return "ansi256"
  return "truecolor"
}

export type ColorToken =
  | { mode: "none"; value: null }
  | { mode: "truecolor"; value: string }
  | { mode: "ansi256"; value: number }
  | { mode: "ansi16"; value: Ansi16ColorName }

export const resolveColorToken = (hex: string, mode: ColorMode): ColorToken => {
  if (mode === "none") return { mode: "none", value: null }
  if (mode === "ansi256") {
    return { mode: "ansi256", value: ANSI256_FALLBACK[hex] ?? 250 }
  }
  if (mode === "ansi16") {
    return { mode: "ansi16", value: ANSI16_FALLBACK[hex] ?? "white" }
  }
  return { mode: "truecolor", value: hex }
}

export type ColorMode = "truecolor" | "ansi256" | "ansi16" | "none"

type Ansi16ColorName =
  | "black"
  | "red"
  | "green"
  | "yellow"
  | "blue"
  | "magenta"
  | "cyan"
  | "white"
  | "gray"
  | "grey"
  | "redBright"
  | "greenBright"
  | "yellowBright"
  | "blueBright"
  | "magentaBright"
  | "cyanBright"
  | "whiteBright"
  | "blackBright"

const ANSI256_FALLBACK: Record<string, number> = {
  [BRAND_COLORS.jamRed]: 203,
  [BRAND_COLORS.strawberryRed]: 205,
  [BRAND_COLORS.duneOrange]: 208,
  [BRAND_COLORS.jumperGreen]: 48,
  [BRAND_COLORS.railBlue]: 75,
  [NEUTRAL_COLORS.nearWhite]: 254,
  [NEUTRAL_COLORS.offWhite]: 253,
  [NEUTRAL_COLORS.lightGray]: 250,
  [NEUTRAL_COLORS.midGray]: 246,
  [NEUTRAL_COLORS.dimGray]: 241,
  [NEUTRAL_COLORS.darkGray]: 238,
  [NEUTRAL_COLORS.nearBlack]: 232,
}

const ANSI16_FALLBACK: Record<string, Ansi16ColorName> = {
  [BRAND_COLORS.jamRed]: "redBright",
  [BRAND_COLORS.strawberryRed]: "magentaBright",
  [BRAND_COLORS.duneOrange]: "yellow",
  [BRAND_COLORS.jumperGreen]: "greenBright",
  [BRAND_COLORS.railBlue]: "blueBright",
  [NEUTRAL_COLORS.nearWhite]: "whiteBright",
  [NEUTRAL_COLORS.offWhite]: "white",
  [NEUTRAL_COLORS.lightGray]: "white",
  [NEUTRAL_COLORS.midGray]: "gray",
  [NEUTRAL_COLORS.dimGray]: "gray",
  [NEUTRAL_COLORS.darkGray]: "gray",
  [NEUTRAL_COLORS.nearBlack]: "black",
}

export const resolveAsciiOnly = (override?: boolean): boolean => {
  if (override != null) return override
  const raw = (process.env.BREADBOARD_ASCII ?? process.env.BREADBOARD_NO_UNICODE ?? "").toString().toLowerCase().trim()
  if (!raw) return false
  return ["1", "true", "yes", "on"].includes(raw)
}

export const resolveIcons = (asciiOnly?: boolean) =>
  resolveAsciiOnly(asciiOnly) ? ASCII_FALLBACK_ICONS : ICONS

export const resolveColorMode = (override?: ColorMode, allowColor = true): ColorMode => {
  if (!allowColor) return "none"
  if (override) return override
  if (process.env.NO_COLOR) return "none"
  const raw = (process.env.BREADBOARD_COLOR_MODE ?? "").toString().toLowerCase().trim()
  if (!raw) return "truecolor"
  if (["0", "none", "off", "false"].includes(raw)) return "none"
  if (["16", "ansi16", "basic"].includes(raw)) return "ansi16"
  if (["256", "ansi256"].includes(raw)) return "ansi256"
  return "truecolor"
}

export type ColorToken =
  | { mode: "none"; value: null }
  | { mode: "truecolor"; value: string }
  | { mode: "ansi256"; value: number }
  | { mode: "ansi16"; value: Ansi16ColorName }

export const resolveColorToken = (hex: string, mode: ColorMode): ColorToken => {
  if (mode === "none") return { mode: "none", value: null }
  if (mode === "ansi256") {
    return { mode: "ansi256", value: ANSI256_FALLBACK[hex] ?? 250 }
  }
  if (mode === "ansi16") {
    return { mode: "ansi16", value: ANSI16_FALLBACK[hex] ?? "white" }
  }
  return { mode: "truecolor", value: hex }
}
