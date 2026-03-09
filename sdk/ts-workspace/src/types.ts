export type ExecutionProfileId =
  | "trusted_local"
  | "constrained_local"
  | "sandboxed_local"
  | "remote_isolated"

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
  shapeToolOutput(text: string, options?: ToolOutputShaperOptions): ToolOutputShape
  supportsProfile(profileId: ExecutionProfileId): boolean
}
