export type ExecutionProfileId =
  | "trusted_local"
  | "constrained_local"
  | "sandboxed_local"
  | "remote_isolated"

export interface ExecutionProfile {
  readonly id: ExecutionProfileId
  readonly summary: string
  readonly placementHint: "local_process" | "oci_container" | "remote_worker"
  readonly securityTierHint: "trusted_dev" | "shared_host" | "single_tenant" | "multi_tenant"
  readonly recommendedFor: readonly string[]
  readonly backendHint: "inline" | "oci" | "remote"
}

export interface WorkspaceCapabilitySet {
  readonly canReadWorkspace: boolean
  readonly canWriteWorkspace: boolean
  readonly canSearchWorkspace: boolean
  readonly canRunTrustedLocal: boolean
  readonly canRunSandboxedLocal: boolean
  readonly canRunRemoteIsolated: boolean
  readonly supportsArtifacts: boolean
}

export interface WorkspaceArtifactRef {
  readonly artifactId: string
  readonly kind: "stdout" | "stderr" | "diff" | "file" | "preview" | "generic"
  readonly location: string
  readonly title?: string | null
  readonly metadata?: Record<string, unknown>
}

export interface ToolOutputShape {
  readonly userVisibleText: string
  readonly modelVisibleText: string
  readonly truncated: boolean
  readonly artifactRefs: readonly WorkspaceArtifactRef[]
}

export interface TerminalOutputShape extends ToolOutputShape {
  readonly chunkCount: number
}

export interface TerminalSessionEndShape {
  readonly terminalState: string | null
  readonly exitCode: number | null
  readonly durationMs: number | null
  readonly artifactRefs: readonly WorkspaceArtifactRef[]
  readonly evidenceRefs: readonly string[]
}

export interface ToolOutputShaperOptions {
  readonly maxChars?: number
  readonly headChars?: number
  readonly tailChars?: number
  readonly stripAnsi?: boolean
  readonly artifactRefs?: readonly WorkspaceArtifactRef[]
}

export interface WorkspaceOptions {
  readonly workspaceId: string
  readonly rootDir?: string | null
  readonly capabilitySet: WorkspaceCapabilitySet
  readonly defaultExecutionProfileId?: ExecutionProfileId
}

export interface Workspace {
  readonly workspaceId: string
  readonly rootDir: string | null
  readonly capabilitySet: WorkspaceCapabilitySet
  readonly defaultExecutionProfileId: ExecutionProfileId
  readonly defaultExecutionProfile: ExecutionProfile
  shapeToolOutput(text: string, options?: ToolOutputShaperOptions): ToolOutputShape
  shapeTerminalOutput(text: string, options?: ToolOutputShaperOptions & { chunkCount?: number }): TerminalOutputShape
  shapeTerminalOutputDeltas(
    outputDeltas: readonly { readonly chunk_b64: string }[],
    options?: ToolOutputShaperOptions,
  ): TerminalOutputShape
  shapeTerminalSessionEnd(end: {
    readonly artifact_refs?: readonly string[]
    readonly evidence_refs?: readonly string[]
    readonly terminal_state?: string | null
    readonly exit_code?: number | null
    readonly duration_ms?: number | null
  }): TerminalSessionEndShape
  supportsProfile(profileId: ExecutionProfileId): boolean
  getExecutionProfile(profileId?: ExecutionProfileId): ExecutionProfile
}
