import type {
  ExecutionProfileId,
  ToolOutputShape,
  ToolOutputShaperOptions,
  Workspace,
  WorkspaceArtifactRef,
  WorkspaceCapabilitySet,
  WorkspaceOptions,
} from "./types.js"

const ANSI_PATTERN = /\u001b\[[0-9;]*m/g

export type {
  ExecutionProfileId,
  ToolOutputShape,
  ToolOutputShaperOptions,
  Workspace,
  WorkspaceArtifactRef,
  WorkspaceCapabilitySet,
  WorkspaceOptions,
} from "./types.js"

export function buildWorkspaceCapabilitySet(
  overrides: Partial<WorkspaceCapabilitySet> = {},
): WorkspaceCapabilitySet {
  return {
    canReadWorkspace: true,
    canWriteWorkspace: true,
    canSearchWorkspace: true,
    canRunTrustedLocal: true,
    canRunSandboxedLocal: false,
    canRunRemoteIsolated: false,
    supportsArtifacts: true,
    ...overrides,
  }
}

export function stripAnsi(text: string): string {
  return text.replace(ANSI_PATTERN, "")
}

export function shapeToolOutput(text: string, options: ToolOutputShaperOptions = {}): ToolOutputShape {
  const normalized = options.stripAnsi === false ? text : stripAnsi(text)
  const maxChars = options.maxChars ?? 1200
  const headChars = options.headChars ?? Math.min(800, maxChars)
  const tailChars = options.tailChars ?? Math.min(300, maxChars)
  const truncated = normalized.length > maxChars
  const visible = truncated
    ? `${normalized.slice(0, headChars)}\n...\n${normalized.slice(-tailChars)}`
    : normalized
  const modelVisibleText = visible.length > 600 ? `${visible.slice(0, 600)}...` : visible
  return {
    userVisibleText: visible,
    modelVisibleText,
    truncated,
    artifactRefs: options.artifactRefs ?? [],
  }
}

function defaultProfileForCapabilities(capabilities: WorkspaceCapabilitySet): ExecutionProfileId {
  if (capabilities.canRunRemoteIsolated) return "remote_isolated"
  if (capabilities.canRunSandboxedLocal) return "sandboxed_local"
  if (capabilities.canRunTrustedLocal) return "trusted_local"
  return "constrained_local"
}

export function supportsExecutionProfile(
  capabilities: WorkspaceCapabilitySet,
  profileId: ExecutionProfileId,
): boolean {
  switch (profileId) {
    case "trusted_local":
      return capabilities.canRunTrustedLocal
    case "constrained_local":
      return capabilities.canReadWorkspace || capabilities.canSearchWorkspace
    case "sandboxed_local":
      return capabilities.canRunSandboxedLocal
    case "remote_isolated":
      return capabilities.canRunRemoteIsolated
  }
}

export function createWorkspace(options: WorkspaceOptions): Workspace {
  const defaultExecutionProfileId =
    options.defaultExecutionProfileId ?? defaultProfileForCapabilities(options.capabilitySet)
  return {
    workspaceId: options.workspaceId,
    rootDir: options.rootDir ?? null,
    capabilitySet: options.capabilitySet,
    defaultExecutionProfileId,
    shapeToolOutput(text: string, shapeOptions?: ToolOutputShaperOptions): ToolOutputShape {
      return shapeToolOutput(text, shapeOptions)
    },
    supportsProfile(profileId: ExecutionProfileId): boolean {
      return supportsExecutionProfile(options.capabilitySet, profileId)
    },
  }
}
