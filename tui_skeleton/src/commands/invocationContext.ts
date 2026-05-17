import { Option } from "effect"
import { parseJsonObject } from "../utils/json.js"
import { resolveBreadboardRepoPath, resolveBreadboardWorkspaceOrCwd } from "../utils/paths.js"

export const DEFAULT_REQUEST_CONFIG_PATH =
  process.env.BREADBOARD_DEFAULT_CONFIG ?? "agent_configs/codex_0-107-0_e4_3-6-2026.yaml"
export const ANALYSIS_REQUEST_CONFIG_PATH =
  process.env.BREADBOARD_ANALYSIS_CONFIG ?? "agent_configs/misc/opencode_openai_gpt5nano_c_fs_cli_analysis.yaml"

export interface ResolveInvocationContextInput {
  readonly config: string
  readonly workspace?: string | null
  readonly overrides?: string | null
  readonly metadata?: string | null
  readonly model?: string | null
  readonly remoteStream?: Option.Option<boolean>
  readonly permissionMode?: string | null
  readonly analysis?: boolean
  readonly fallbackModel?: string | null
}

export interface InvocationContext {
  readonly resolvedConfigPath: string
  readonly resolvedWorkspace: string
  readonly remotePreference: boolean | undefined
  readonly permissionValue: string | null
  readonly resolvedModel: string | null
  readonly requestOverrides: Record<string, unknown>
  readonly requestMetadata: Record<string, unknown>
}

export const resolveInvocationContext = (input: ResolveInvocationContextInput): InvocationContext => {
  const requestOverrides = input.overrides ? parseJsonObject(input.overrides, "--overrides") : {}
  const requestMetadata = input.metadata ? parseJsonObject(input.metadata, "--metadata") : {}
  const overrideModelValue =
    typeof requestOverrides["providers.default_model"] === "string"
      ? (requestOverrides["providers.default_model"] as string)
      : undefined
  const resolvedModel = input.model ?? overrideModelValue ?? input.fallbackModel ?? null
  const permissionValue = input.permissionMode ?? null
  const permissionModeValue = permissionValue ? permissionValue.toLowerCase() : ""
  const interactivePermissions =
    permissionModeValue === "prompt" || permissionModeValue === "ask" || permissionModeValue === "interactive"

  if (resolvedModel) {
    requestMetadata.model = resolvedModel
    requestOverrides["providers.default_model"] = resolvedModel
  }

  if (permissionValue) {
    requestMetadata.permission_mode = permissionValue
    if (interactivePermissions) {
      requestOverrides["permissions.options.mode"] ??= "prompt"
      requestOverrides["permissions.options.default_response"] ??= "reject"
      requestOverrides["permissions.edit.default"] ??= "ask"
      requestOverrides["permissions.shell.default"] ??= "ask"
      requestOverrides["permissions.webfetch.default"] ??= "ask"
    }
  }

  let configPath = input.config
  if (input.analysis) {
    if (!configPath || configPath === DEFAULT_REQUEST_CONFIG_PATH) {
      configPath = ANALYSIS_REQUEST_CONFIG_PATH
    }
    requestMetadata.mode = "analysis"
    if (!permissionValue) {
      requestMetadata.permission_mode = "analysis"
    }
  }

  return {
    resolvedConfigPath: resolveBreadboardRepoPath(configPath),
    resolvedWorkspace: resolveBreadboardWorkspaceOrCwd(input.workspace ?? null),
    remotePreference: Option.match(input.remoteStream ?? Option.none<boolean>(), {
      onNone: () => undefined,
      onSome: (value) => value,
    }),
    permissionValue,
    resolvedModel,
    requestOverrides,
    requestMetadata,
  }
}
