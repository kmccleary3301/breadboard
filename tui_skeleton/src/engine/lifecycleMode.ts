export type EngineLifecycleMode = "local-owned" | "external" | "remote" | "off"

export type EngineLifecycleModeSource =
  | "cli"
  | "config"
  | "env"
  | "explicit-base-url"
  | "default-local"
  | "default-remote"
  | "command-skip"

export type EngineRestartPolicy = "bounded" | "external-only" | "remote-only" | "disabled"

export type EngineLifecycleActionPolicy = "allowed" | "forbidden" | "mode-dependent"

export interface EngineLifecycleModeContract {
  readonly mode: EngineLifecycleMode
  readonly spawn: EngineLifecycleActionPolicy
  readonly attach: EngineLifecycleActionPolicy
  readonly health: EngineLifecycleActionPolicy
  readonly restart: EngineLifecycleActionPolicy
  readonly kill: EngineLifecycleActionPolicy
  readonly shutdown: EngineLifecycleActionPolicy
  readonly logs: EngineLifecycleActionPolicy
  readonly transcriptPolicy: "runtime-only" | "diagnostic-only"
  readonly footerPolicy: "show-mode-and-recovery" | "show-unowned-boundary" | "show-remote-boundary" | "hidden"
}

export interface EngineLifecycleStatusContract {
  readonly key: string
  readonly label: string
  readonly composerAcceptsInput: boolean
  readonly preservesDraft: boolean
  readonly unresolvedTurn: boolean
  readonly transcriptBodyAllowed: boolean
  readonly recoveryAction:
    | "send"
    | "wait"
    | "retry"
    | "resume"
    | "restart"
    | "external-restart"
    | "remote-retry"
    | "exit"
}

export const ENGINE_LIFECYCLE_MODES = ["local-owned", "external", "remote", "off"] as const satisfies readonly EngineLifecycleMode[]

export const ENGINE_LIFECYCLE_MODE_CONTRACT: Record<EngineLifecycleMode, EngineLifecycleModeContract> = {
  "local-owned": {
    mode: "local-owned",
    spawn: "allowed",
    attach: "allowed",
    health: "allowed",
    restart: "allowed",
    kill: "allowed",
    shutdown: "mode-dependent",
    logs: "allowed",
    transcriptPolicy: "runtime-only",
    footerPolicy: "show-mode-and-recovery",
  },
  external: {
    mode: "external",
    spawn: "forbidden",
    attach: "allowed",
    health: "allowed",
    restart: "forbidden",
    kill: "forbidden",
    shutdown: "forbidden",
    logs: "mode-dependent",
    transcriptPolicy: "runtime-only",
    footerPolicy: "show-unowned-boundary",
  },
  remote: {
    mode: "remote",
    spawn: "forbidden",
    attach: "allowed",
    health: "allowed",
    restart: "forbidden",
    kill: "forbidden",
    shutdown: "forbidden",
    logs: "mode-dependent",
    transcriptPolicy: "runtime-only",
    footerPolicy: "show-remote-boundary",
  },
  off: {
    mode: "off",
    spawn: "forbidden",
    attach: "forbidden",
    health: "forbidden",
    restart: "forbidden",
    kill: "forbidden",
    shutdown: "forbidden",
    logs: "forbidden",
    transcriptPolicy: "diagnostic-only",
    footerPolicy: "hidden",
  },
}

export const ENGINE_LIFECYCLE_STATUS_CONTRACT = {
  ready: {
    key: "ready",
    label: "[ready]",
    composerAcceptsInput: true,
    preservesDraft: true,
    unresolvedTurn: false,
    transcriptBodyAllowed: false,
    recoveryAction: "send",
  },
  working: {
    key: "working",
    label: "[working]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: true,
    transcriptBodyAllowed: false,
    recoveryAction: "wait",
  },
  responding: {
    key: "responding",
    label: "[responding]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: true,
    transcriptBodyAllowed: false,
    recoveryAction: "wait",
  },
  reconnecting: {
    key: "reconnecting",
    label: "[reconnecting]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: true,
    transcriptBodyAllowed: false,
    recoveryAction: "wait",
  },
  "restarting-engine": {
    key: "restarting-engine",
    label: "[restarting engine]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: true,
    transcriptBodyAllowed: false,
    recoveryAction: "wait",
  },
  reattaching: {
    key: "reattaching",
    label: "[reattaching]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: true,
    transcriptBodyAllowed: false,
    recoveryAction: "wait",
  },
  recovered: {
    key: "recovered",
    label: "[recovered]",
    composerAcceptsInput: true,
    preservesDraft: true,
    unresolvedTurn: false,
    transcriptBodyAllowed: false,
    recoveryAction: "send",
  },
  "external-disconnected": {
    key: "external-disconnected",
    label: "[external disconnected]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: false,
    transcriptBodyAllowed: false,
    recoveryAction: "external-restart",
  },
  "remote-disconnected": {
    key: "remote-disconnected",
    label: "[remote disconnected]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: false,
    transcriptBodyAllowed: false,
    recoveryAction: "remote-retry",
  },
  "recovery-needed": {
    key: "recovery-needed",
    label: "[recovery needed]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: true,
    transcriptBodyAllowed: false,
    recoveryAction: "retry",
  },
  failed: {
    key: "failed",
    label: "[failed]",
    composerAcceptsInput: false,
    preservesDraft: true,
    unresolvedTurn: false,
    transcriptBodyAllowed: false,
    recoveryAction: "exit",
  },
} as const satisfies Record<string, EngineLifecycleStatusContract>

export interface ResolvedEngineLifecycleMode {
  readonly mode: EngineLifecycleMode
  readonly modeSource: EngineLifecycleModeSource
  readonly owned: boolean
  readonly allowSpawn: boolean
  readonly restartPolicy: EngineRestartPolicy
  readonly isLocalBaseUrl: boolean
  readonly reason: string
}

export interface ResolveEngineLifecycleModeOptions {
  readonly cliMode?: string | null
  readonly configMode?: string | null
  readonly envMode?: string | null
  readonly baseUrl?: string | null
  readonly explicitBaseUrlConfigured?: boolean
  readonly commandSkipsEngine?: boolean
}

export const isLocalEngineBaseUrl = (value: string | null | undefined): boolean => {
  if (!value?.trim()) return true
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(value.trim()) ? value.trim() : `http://${value.trim()}`
  try {
    const url = new URL(candidate)
    const host = url.hostname.toLowerCase()
    return host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "[::1]"
  } catch {
    return false
  }
}

export const normalizeEngineLifecycleMode = (value: string | null | undefined): EngineLifecycleMode | null => {
  const normalized = value?.trim().toLowerCase()
  if (!normalized) return null
  if (["local-owned", "local_owned", "local", "owned", "managed", "spawn", "auto"].includes(normalized)) {
    return "local-owned"
  }
  if (["external", "client", "attached"].includes(normalized)) return "external"
  if (["remote"].includes(normalized)) return "remote"
  if (["off", "none", "disabled", "skip"].includes(normalized)) return "off"
  return null
}

const policyForMode = (mode: EngineLifecycleMode): EngineRestartPolicy => {
  if (mode === "local-owned") return "bounded"
  if (mode === "external") return "external-only"
  if (mode === "remote") return "remote-only"
  return "disabled"
}

const resolved = (
  mode: EngineLifecycleMode,
  modeSource: EngineLifecycleModeSource,
  isLocalBaseUrl: boolean,
  reason: string,
): ResolvedEngineLifecycleMode => ({
  mode,
  modeSource,
  owned: mode === "local-owned",
  allowSpawn: mode === "local-owned",
  restartPolicy: policyForMode(mode),
  isLocalBaseUrl,
  reason,
})

export const resolveEngineLifecycleMode = ({
  cliMode,
  configMode,
  envMode,
  baseUrl,
  explicitBaseUrlConfigured = false,
  commandSkipsEngine = false,
}: ResolveEngineLifecycleModeOptions): ResolvedEngineLifecycleMode => {
  const local = isLocalEngineBaseUrl(baseUrl)
  if (commandSkipsEngine) {
    return resolved("off", "command-skip", local, "The selected command does not require an engine.")
  }

  const cli = normalizeEngineLifecycleMode(cliMode)
  if (cli) return resolved(cli, "cli", local, "Lifecycle mode was selected by CLI argument.")

  const config = normalizeEngineLifecycleMode(configMode)
  if (config) return resolved(config, "config", local, "Lifecycle mode was selected by user config.")

  const env = normalizeEngineLifecycleMode(envMode)
  if (env) return resolved(env, "env", local, "Lifecycle mode was selected by BREADBOARD_ENGINE_MODE.")

  if (explicitBaseUrlConfigured) {
    if (local) {
      return resolved(
        "local-owned",
        "explicit-base-url",
        local,
        "Explicit local base URL is treated as an owned engine unless external ownership is explicitly requested.",
      )
    }
    return resolved(
      "remote",
      "explicit-base-url",
      local,
      "Explicit non-local base URL is treated as a remote engine.",
    )
  }

  if (local) {
    return resolved("local-owned", "default-local", local, "Plain local bb defaults to an owned local engine.")
  }
  return resolved("remote", "default-remote", local, "Non-local default base URL is treated as a remote engine.")
}
