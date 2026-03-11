import type {
  ExecutionProfile,
  ExecutionProfileId,
  TerminalOutputShape,
  TerminalSessionEndShape,
  ToolOutputShape,
  ToolOutputShaperOptions,
  Workspace,
  WorkspaceArtifactRef,
  WorkspaceCapabilitySet,
  WorkspaceOptions,
} from "./types.js"

const ANSI_PATTERN = /\u001b\[[0-9;]*m/g

export type {
  ExecutionProfile,
  ExecutionProfileId,
  TerminalOutputShape,
  TerminalSessionEndShape,
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

export function shapeTerminalOutput(
  text: string,
  options: ToolOutputShaperOptions & { chunkCount?: number } = {},
): TerminalOutputShape {
  return {
    ...shapeToolOutput(text, options),
    chunkCount: options.chunkCount ?? 0,
  }
}

export function shapeTerminalOutputDeltas(
  outputDeltas: readonly { readonly chunk_b64: string }[],
  options: ToolOutputShaperOptions = {},
): TerminalOutputShape {
  const text = outputDeltas
    .map((delta) => Buffer.from(delta.chunk_b64, "base64").toString("utf8"))
    .join("")
  return shapeTerminalOutput(text, {
    ...options,
    chunkCount: outputDeltas.length,
  })
}

function shapeArtifactRefs(locations: readonly string[] | undefined): readonly WorkspaceArtifactRef[] {
  return (locations ?? []).map((location) => ({
    artifactId: location,
    kind: "generic" as const,
    location,
  }))
}

export function shapeTerminalSessionEnd(end: {
  readonly artifact_refs?: readonly string[]
  readonly evidence_refs?: readonly string[]
  readonly terminal_state?: string | null
  readonly exit_code?: number | null
  readonly duration_ms?: number | null
}): TerminalSessionEndShape {
  return {
    terminalState: end.terminal_state ?? null,
    exitCode: end.exit_code ?? null,
    durationMs: end.duration_ms ?? null,
    artifactRefs: shapeArtifactRefs(end.artifact_refs),
    evidenceRefs: [...(end.evidence_refs ?? [])],
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

export function createExecutionProfile(profileId: ExecutionProfileId): ExecutionProfile {
  switch (profileId) {
    case "trusted_local":
      return {
        id: profileId,
        summary: "Trusted local execution with direct process access.",
        placementHint: "local_process",
        securityTierHint: "trusted_dev",
        recommendedFor: ["local developer workflows", "trusted repos", "fast inner-loop runs"],
        backendHint: "inline",
      }
    case "constrained_local":
      return {
        id: profileId,
        summary: "Constrained local execution with reduced capability assumptions.",
        placementHint: "local_process",
        securityTierHint: "shared_host",
        recommendedFor: ["shared workstations", "lower-trust local commands", "restricted host paths"],
        backendHint: "inline",
      }
    case "sandboxed_local":
      return {
        id: profileId,
        summary: "Sandboxed local execution mediated by OCI-class isolation.",
        placementHint: "oci_container",
        securityTierHint: "single_tenant",
        recommendedFor: ["containerized tool turns", "stronger local isolation", "OCI-backed automation"],
        backendHint: "oci",
      }
    case "remote_isolated":
      return {
        id: profileId,
        summary: "Delegated remote execution behind an isolated worker boundary.",
        placementHint: "remote_worker",
        securityTierHint: "multi_tenant",
        recommendedFor: ["untrusted code paths", "remote workers", "shared fleet execution"],
        backendHint: "remote",
      }
  }
}

export function createWorkspace(options: WorkspaceOptions): Workspace {
  const defaultExecutionProfileId =
    options.defaultExecutionProfileId ?? defaultProfileForCapabilities(options.capabilitySet)
  const defaultExecutionProfile = createExecutionProfile(defaultExecutionProfileId)
  return {
    workspaceId: options.workspaceId,
    rootDir: options.rootDir ?? null,
    capabilitySet: options.capabilitySet,
    defaultExecutionProfileId,
    defaultExecutionProfile,
    shapeToolOutput(text: string, shapeOptions?: ToolOutputShaperOptions): ToolOutputShape {
      return shapeToolOutput(text, shapeOptions)
    },
    shapeTerminalOutput(text: string, shapeOptions?: ToolOutputShaperOptions & { chunkCount?: number }): TerminalOutputShape {
      return shapeTerminalOutput(text, shapeOptions)
    },
    shapeTerminalOutputDeltas(
      outputDeltas: readonly { readonly chunk_b64: string }[],
      shapeOptions?: ToolOutputShaperOptions,
    ): TerminalOutputShape {
      return shapeTerminalOutputDeltas(outputDeltas, shapeOptions)
    },
    shapeTerminalSessionEnd(end): TerminalSessionEndShape {
      return shapeTerminalSessionEnd(end)
    },
    supportsProfile(profileId: ExecutionProfileId): boolean {
      return supportsExecutionProfile(options.capabilitySet, profileId)
    },
    getExecutionProfile(profileId?: ExecutionProfileId): ExecutionProfile {
      return createExecutionProfile(profileId ?? defaultExecutionProfileId)
    },
  }
}
