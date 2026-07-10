import { Option } from "effect"
import { DEFAULT_CONFIG_PATH } from "../../config/appConfig.js"
import {
  resolveBreadboardFrontend,
  resolveLiveShellOwnershipMode,
  resolveLiveShellRendererHost,
  resolveLiveShellSceneStrategy,
  type BreadboardFrontend,
  type LiveShellOwnershipMode,
  type LiveShellRendererHost,
  type LiveShellSceneStrategy,
} from "../../config/frontendMode.js"
import { resolveTuiConfig } from "../../tui_config/load.js"
import type { ResolvedTuiConfig } from "../../tui_config/types.js"
import { resolveBreadboardRepoPath, resolveBreadboardWorkspaceOrCwd } from "../../utils/paths.js"

export const DEFAULT_REPL_CONFIG_PATH = DEFAULT_CONFIG_PATH

export interface ReplLaunchInputs {
  readonly config: string
  readonly workspace?: string | null
  readonly model?: string | null
  readonly remoteStream?: Option.Option<boolean>
  readonly permissionMode?: string | null
  readonly scriptPath?: string | null
  readonly tui?: string | null
  readonly tuiPreset?: string | null
  readonly tuiConfigPath?: string | null
  readonly tuiConfigStrict?: boolean | null
}

export interface ReplLaunchContext {
  readonly frontend: BreadboardFrontend
  readonly liveShellOwnershipMode: LiveShellOwnershipMode
  readonly liveShellRendererHost: LiveShellRendererHost
  readonly liveShellSceneStrategy: LiveShellSceneStrategy
  readonly resolvedConfigPath: string
  readonly resolvedWorkspace: string
  readonly modelValue: string | null
  readonly permissionValue: string | null
  readonly remotePreference: boolean | undefined
  readonly scriptPath: string | null
  readonly resolvedTuiConfig: ResolvedTuiConfig
}

export const resolveReplLaunchContext = async (input: ReplLaunchInputs): Promise<ReplLaunchContext> => {
  const scriptPath = input.scriptPath ?? null
  const frontend = resolveBreadboardFrontend({ scriptPath, cliValue: input.tui ?? null })
  const liveShellOwnershipMode = resolveLiveShellOwnershipMode()
  const liveShellRendererHost = resolveLiveShellRendererHost()
  const liveShellSceneStrategy = resolveLiveShellSceneStrategy()
  const resolvedConfigPath = resolveBreadboardRepoPath(input.config)
  const resolvedWorkspace = resolveBreadboardWorkspaceOrCwd(input.workspace ?? null)
  const remotePreference = Option.match(input.remoteStream ?? Option.none<boolean>(), {
    onNone: () => undefined,
    onSome: (value) => value,
  })
  const resolvedTuiConfig = await resolveTuiConfig({
    workspace: resolvedWorkspace,
    cliPreset: input.tuiPreset ?? null,
    cliConfigPath: input.tuiConfigPath ?? null,
    cliStrict: input.tuiConfigStrict ?? null,
  })
  return {
    frontend,
    liveShellOwnershipMode,
    liveShellRendererHost,
    liveShellSceneStrategy,
    resolvedConfigPath,
    resolvedWorkspace,
    modelValue: input.model ?? null,
    permissionValue: input.permissionMode ?? null,
    remotePreference,
    scriptPath,
    resolvedTuiConfig,
  }
}
