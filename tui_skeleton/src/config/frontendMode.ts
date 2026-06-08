export type BreadboardFrontend = "scrollback" | "opentui"
export type ScrollbackPresentationMode = "scrollback" | "window"
export type LiveShellOwnershipMode = "inline-scrollback" | "owned-live"
export type LiveShellRendererHost = "ink-managed" | "escalated-owned"
export type LiveShellSceneStrategy = "scene-owned-runtime" | "dedicated-scene-buffer"

const normalize = (value?: string | null): string => (value ?? "").trim().toLowerCase()

export const normalizeBreadboardFrontend = (value?: string | null): BreadboardFrontend | null => {
  const normalized = normalize(value)
  if (normalized === "scrollback" || normalized === "classic") return "scrollback"
  if (normalized === "opentui") return "opentui"
  return null
}

export const normalizeScrollbackPresentationMode = (value?: string | null): ScrollbackPresentationMode | null => {
  const normalized = normalize(value)
  if (normalized === "scrollback") return "scrollback"
  if (normalized === "window" || normalized === "compact") return "window"
  return null
}

export const normalizeLiveShellOwnershipMode = (value?: string | null): LiveShellOwnershipMode | null => {
  const normalized = normalize(value)
  if (normalized === "inline-scrollback" || normalized === "inline" || normalized === "scrollback") {
    return "inline-scrollback"
  }
  if (normalized === "owned-live" || normalized === "owned" || normalized === "owned-live-shell") {
    return "owned-live"
  }
  return null
}

export const normalizeLiveShellRendererHost = (value?: string | null): LiveShellRendererHost | null => {
  const normalized = normalize(value)
  if (normalized === "ink-managed" || normalized === "ink" || normalized === "managed" || normalized === "current") {
    return "ink-managed"
  }
  if (
    normalized === "escalated-owned" ||
    normalized === "escalated" ||
    normalized === "owned-viewport" ||
    normalized === "renderer-escalated"
  ) {
    return "escalated-owned"
  }
  return null
}

export const normalizeLiveShellSceneStrategy = (value?: string | null): LiveShellSceneStrategy | null => {
  const normalized = normalize(value)
  if (
    normalized === "scene-owned-runtime" ||
    normalized === "scene-owned" ||
    normalized === "runtime-scene-owned" ||
    normalized === "s1"
  ) {
    return "scene-owned-runtime"
  }
  if (
    normalized === "dedicated-scene-buffer" ||
    normalized === "scene-buffer" ||
    normalized === "dedicated-buffer" ||
    normalized === "s2"
  ) {
    return "dedicated-scene-buffer"
  }
  return null
}

export const resolveBreadboardFrontend = (args: {
  readonly scriptPath?: string | null
  readonly cliValue?: string | null
  readonly env?: NodeJS.ProcessEnv
}): BreadboardFrontend => {
  if (args.scriptPath) return "scrollback"
  const cliMode = normalizeBreadboardFrontend(args.cliValue)
  if (cliMode) return cliMode
  const env = args.env ?? process.env
  const explicit = normalizeBreadboardFrontend(env.BREADBOARD_TUI_FRONTEND)
  if (explicit) return explicit
  const compat = normalizeBreadboardFrontend(env.BREADBOARD_TUI_MODE)
  if (compat) return compat
  return "scrollback"
}

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

export const resolveScrollbackPresentationMode = (env: NodeJS.ProcessEnv = process.env): ScrollbackPresentationMode => {
  const explicit = normalizeScrollbackPresentationMode(env.BREADBOARD_SCROLLBACK_MODE)
  if (explicit) return explicit
  const compat = normalizeScrollbackPresentationMode(env.BREADBOARD_TUI_MODE)
  if (compat) return compat
  return parseBoolEnv(env.BREADBOARD_TUI_SCROLLBACK, true) ? "scrollback" : "window"
}

export const resolveScrollbackEnabled = (env: NodeJS.ProcessEnv = process.env): boolean =>
  resolveScrollbackPresentationMode(env) === "scrollback"

export const resolveLiveShellOwnershipMode = (
  env: NodeJS.ProcessEnv = process.env,
): LiveShellOwnershipMode => {
  const explicit = normalizeLiveShellOwnershipMode(
    env.BREADBOARD_TUI_LIVE_SHELL_MODE ?? env.BREADBOARD_TUI_OWNERSHIP_MODE,
  )
  if (explicit) return explicit
  return "inline-scrollback"
}

export const resolveOwnedLiveShellEnabled = (env: NodeJS.ProcessEnv = process.env): boolean =>
  resolveLiveShellOwnershipMode(env) === "owned-live"

export const resolveLiveShellRendererHost = (
  env: NodeJS.ProcessEnv = process.env,
): LiveShellRendererHost => {
  const explicit = normalizeLiveShellRendererHost(env.BREADBOARD_TUI_LIVE_SHELL_HOST)
  if (explicit) return explicit
  return resolveLiveShellOwnershipMode(env) === "owned-live" ? "escalated-owned" : "ink-managed"
}

export const resolveLiveShellSceneStrategy = (
  env: NodeJS.ProcessEnv = process.env,
): LiveShellSceneStrategy => {
  const explicit = normalizeLiveShellSceneStrategy(
    env.BREADBOARD_TUI_SCENE_OWNED_STRATEGY ?? env.BREADBOARD_TUI_RENDERER_STRATEGY,
  )
  if (explicit) return explicit
  return "scene-owned-runtime"
}

export const resolveFrontendSelectionFromArgv = (argv: ReadonlyArray<string>, env: NodeJS.ProcessEnv = process.env): BreadboardFrontend => {
  const command = argv[2]
  const tuiIndex = argv.indexOf("--tui")
  const cliValue = tuiIndex >= 0 ? argv[tuiIndex + 1] ?? null : null
  return resolveBreadboardFrontend({
    scriptPath: command === "repl" || command === "ui" ? (argv.includes("--script") ? "inline-script" : null) : null,
    cliValue,
    env,
  })
}
