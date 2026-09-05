import { createHash, randomUUID } from "node:crypto"
import { lstatSync, readFileSync } from "node:fs"
import { chmod, lstat, mkdir, rename, rm, writeFile } from "node:fs/promises"
import { homedir, tmpdir } from "node:os"
import { join } from "node:path"
import { pathToFileURL } from "node:url"

import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
  TerminalCleanupResultV1,
  TerminalInteractionV1,
  TerminalOutputDeltaV1,
  TerminalRegistrySnapshotV1,
  TerminalSessionDescriptorV1,
  TerminalSessionEndV1,
  UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"

export interface ExecutionDriverV1 {
  driverId: string
  supportedPlacements: ExecutionPlacementV1["placement_class"][]
  supportsCapability(capability: ExecutionCapabilityV1, placementClass: ExecutionPlacementV1["placement_class"]): boolean
  buildSandboxRequest?(input: {
    requestId: string
    capability: ExecutionCapabilityV1
    placement: ExecutionPlacementV1
    command: string[]
    workspaceRef?: string | null
    imageRef?: string | null
    metadata?: Record<string, unknown>
  }): SandboxRequestV1
  execute?(
    request: SandboxRequestV1,
    context?: {
      signal: AbortSignal
      deadlineAtMs: number | null
      terminationGraceMs: number
      capability: ExecutionCapabilityV1
      placement: ExecutionPlacementV1
      driverId: string
    },
  ): Promise<SandboxResultV1>
  terminate?(
    request: SandboxRequestV1,
    context: {
      reason: "deadline" | "cancelled"
      signal: AbortSignal
      deadlineAtMs: number | null
    },
  ): Promise<SandboxResultV1 | void> | SandboxResultV1 | void
}

export interface TerminalSessionStartInputV1 {
  terminalSessionId: string
  command: string[]
  cwd?: string | null
  startupCallId?: string | null
  ownerTaskId?: string | null
  publicHandles?: TerminalSessionDescriptorV1["public_handles"]
  capability?: ExecutionCapabilityV1 | null
  placement?: ExecutionPlacementV1 | null
  persistenceScope?: TerminalSessionDescriptorV1["persistence_scope"]
  continuationScope?: TerminalSessionDescriptorV1["continuation_scope"]
  streamMode?: TerminalSessionDescriptorV1["stream_mode"]
  streamSplit?: TerminalSessionDescriptorV1["stream_split"]
}

export interface TerminalSessionStartResultV1 {
  descriptor: TerminalSessionDescriptorV1
  outputDeltas: TerminalOutputDeltaV1[]
  end?: TerminalSessionEndV1
}

export interface TerminalSessionInteractionInputV1 {
  terminalSessionId: string
  interactionKind: TerminalInteractionV1["interaction_kind"]
  causingCallId?: string | null
  inputText?: string | null
  inputB64?: string | null
  signal?: string | null
  settleMs?: number
}

export interface TerminalSessionInteractionResultV1 {
  interaction: TerminalInteractionV1
  outputDeltas: TerminalOutputDeltaV1[]
  end?: TerminalSessionEndV1
}

export interface TerminalSessionCleanupInputV1 {
  cleanupId: string
  scope: TerminalCleanupResultV1["scope"]
  sessionIds?: string[]
  signal?: string | null
}

export interface TerminalSessionDriverV1 extends ExecutionDriverV1 {
  supportsTerminalSessions?(
    capability: ExecutionCapabilityV1,
    placementClass: ExecutionPlacementV1["placement_class"],
  ): boolean
  startTerminalSession?(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1>
  interactTerminalSession?(input: TerminalSessionInteractionInputV1): Promise<TerminalSessionInteractionResultV1>
  snapshotTerminalRegistry?(): Promise<TerminalRegistrySnapshotV1>
  cleanupTerminalSessions?(input: TerminalSessionCleanupInputV1): Promise<TerminalCleanupResultV1>
}

export function selectTerminalSessionDriver(input: {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  drivers: TerminalSessionDriverV1[]
}): TerminalSessionDriverV1 | null {
  return (
    input.drivers.find((driver) => {
      if (!driver.supportedPlacements.includes(input.placement.placement_class)) {
        return false
      }
      if (typeof driver.supportsTerminalSessions === "function") {
        return driver.supportsTerminalSessions(input.capability, input.placement.placement_class)
      }
      const hasTerminalMethod =
        typeof driver.startTerminalSession === "function" ||
        typeof driver.interactTerminalSession === "function" ||
        typeof driver.snapshotTerminalRegistry === "function" ||
        typeof driver.cleanupTerminalSessions === "function"
      return (
        hasTerminalMethod &&
        driver.supportsCapability(input.capability, input.placement.placement_class)
      )
    }) ?? null
  )
}

export interface ExecutionDriverSideEffectExpectationV1 {
  filesystem_scope: "none" | "workspace_scoped" | "container_scoped" | "remote_scoped"
  network_scope: "disabled" | "restricted" | "backend_policy"
  process_scope: "none" | "local_process" | "sandbox_process" | "remote_process"
  persistence_scope: "none" | "workspace_artifacts" | "backend_artifacts"
}

export interface ExecutionDriverEvidenceExpectationV1 {
  require_stdout_ref: boolean
  require_stderr_ref: boolean
  require_artifact_refs: boolean
  require_side_effect_digest: boolean
  require_evidence_refs: boolean
  require_usage_metadata: boolean
}

export interface PlannedExecutionV1 {
  driver: ExecutionDriverV1
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  sandboxRequest: SandboxRequestV1 | null
  sideEffectExpectation: ExecutionDriverSideEffectExpectationV1
  evidenceExpectation: ExecutionDriverEvidenceExpectationV1
}

export function assertExecutionProgramAllowed(
  capability: ExecutionCapabilityV1,
  command: readonly string[],
): void {
  const program = command[0]
  if (!program) {
    throw new Error("Execution requires a non-empty command")
  }
  const allowedPrograms = capability.allow_run_programs
  if (allowedPrograms !== undefined && !allowedPrograms.includes(program)) {
    throw new Error(
      `Execution program ${JSON.stringify(program)} is not allowed by capability ${capability.capability_id}`,
    )
  }
}


export function isPlacementCompatible(
  capability: ExecutionCapabilityV1,
  placementClass: ExecutionPlacementV1["placement_class"],
): boolean {
  const allowedByIsolation: Record<ExecutionCapabilityV1["isolation_class"], ExecutionPlacementV1["placement_class"][]> = {
    none: ["inline_ts"],
    process: ["local_process"],
    oci: ["local_oci"],
    gvisor: ["local_oci_gvisor"],
    kata: ["local_oci_kata"],
    microvm: ["local_microvm"],
    remote_service: ["remote_worker", "delegated_python", "delegated_oci", "delegated_microvm"],
  }
  return allowedByIsolation[capability.isolation_class].includes(placementClass)
}

export function buildExecutionDriverUnsupportedCase(input: {
  capability: ExecutionCapabilityV1
  placementClass: ExecutionPlacementV1["placement_class"]
  fallbackAllowed?: boolean
  fallbackTaken?: boolean
  summary?: string
  reasonCode?: string
  metadata?: Record<string, unknown>
}): UnsupportedCaseV1 {
  return {
    schema_version: "bb.unsupported_case.v1",
    reason_code: input.reasonCode ?? (typeof input.metadata?.reason_code === "string" ? input.metadata.reason_code : "unsupported_execution_driver"),
    summary:
      input.summary ??
      `No execution driver supports ${input.placementClass} for isolation ${input.capability.isolation_class}`,
    contract_family: "bb.execution_placement.v1",
    fallback_allowed: input.fallbackAllowed ?? false,
    fallback_taken: input.fallbackTaken ?? false,
    required_capability_id: input.capability.capability_id,
    unavailable_placement: input.placementClass,
    evidence_refs: [],
    metadata: input.metadata ?? {},
  }
}

export function buildExecutionDriverSideEffectExpectation(
  placementClass: ExecutionPlacementV1["placement_class"],
): ExecutionDriverSideEffectExpectationV1 {
  const mapping: Record<ExecutionPlacementV1["placement_class"], ExecutionDriverSideEffectExpectationV1> = {
    inline_ts: {
      filesystem_scope: "none",
      network_scope: "disabled",
      process_scope: "none",
      persistence_scope: "none",
    },
    local_process: {
      filesystem_scope: "workspace_scoped",
      network_scope: "restricted",
      process_scope: "local_process",
      persistence_scope: "workspace_artifacts",
    },
    local_oci: {
      filesystem_scope: "container_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    local_oci_gvisor: {
      filesystem_scope: "container_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    local_oci_kata: {
      filesystem_scope: "container_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    local_microvm: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    remote_worker: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
    delegated_python: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
    delegated_oci: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
    delegated_microvm: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
  }
  return mapping[placementClass]
}

export interface CanonicalSandboxEvidenceV1 {
  readonly stdout_ref: string
  readonly stderr_ref: string
  readonly artifact_refs: string[]
  readonly side_effect_digest: string
  readonly usage: { readonly exit_code: number | null }
  readonly evidence_refs: string[]
}

function canonicalSandboxDigest(value: string): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`
}

export function canonicalProcessExitCode(
  exitCode: number | null,
  signalNumber: number | null,
): number | null {
  if (signalNumber !== null) {
    if (!Number.isSafeInteger(signalNumber) || signalNumber < 0) {
      throw new Error("process signal number must be a non-negative safe integer")
    }
    if (signalNumber > 0) {
      const signaledExitCode = 128 + signalNumber
      if (!Number.isSafeInteger(signaledExitCode)) {
        throw new Error("canonical signaled exit code exceeds safe integer range")
      }
      return signaledExitCode
    }
  }
  if (
    exitCode !== null
    && (!Number.isSafeInteger(exitCode) || exitCode < 0)
  ) {
    throw new Error("process exit code must be a non-negative safe integer")
  }
  return exitCode
}

function buildCanonicalSandboxSideEffectManifest(input: {
  command: readonly string[]
  status: SandboxResultV1["status"]
  exitCode: number | null
  stdoutRef: string
  stderrRef: string
}): string {
  return JSON.stringify({
    command: input.command,
    exit_code: input.exitCode,
    status: input.status,
    stderr_ref: input.stderrRef,
    stdout_ref: input.stdoutRef,
  })
}

export function buildCanonicalSandboxSideEffectDigest(input: {
  command: readonly string[]
  status: SandboxResultV1["status"]
  exitCode: number | null
  stdoutRef: string
  stderrRef: string
}): string {
  return canonicalSandboxDigest(buildCanonicalSandboxSideEffectManifest(input))
}

function deriveCanonicalSandboxEvidence(input: {
  command: readonly string[]
  status: SandboxResultV1["status"]
  exitCode: number | null
  stdout: string
  stderr: string
  evidenceMode: SandboxRequestV1["evidence_mode"]
}): {
  evidence: CanonicalSandboxEvidenceV1
  sideEffectManifest: string
} {
  const stdoutRef = canonicalSandboxDigest(input.stdout)
  const stderrRef = canonicalSandboxDigest(input.stderr)
  const sideEffectManifest = buildCanonicalSandboxSideEffectManifest({
    command: input.command,
    status: input.status,
    exitCode: input.exitCode,
    stdoutRef,
    stderrRef,
  })
  const sideEffectDigest = canonicalSandboxDigest(sideEffectManifest)
  return {
    evidence: {
      stdout_ref: stdoutRef,
      stderr_ref: stderrRef,
      artifact_refs: [stdoutRef, stderrRef],
      side_effect_digest: sideEffectDigest,
      usage: { exit_code: input.exitCode },
      evidence_refs: input.evidenceMode === "minimal" ? [] : [sideEffectDigest],
    },
    sideEffectManifest,
  }
}

export function buildCanonicalSandboxEvidence(input: {
  command: readonly string[]
  status: SandboxResultV1["status"]
  exitCode: number | null
  stdout: string
  stderr: string
  evidenceMode: SandboxRequestV1["evidence_mode"]
}): CanonicalSandboxEvidenceV1 {
  return deriveCanonicalSandboxEvidence(input).evidence
}

export function canonicalSandboxArtifactRoot(
  parentRoot = join(homedir(), ".breadboard"),
): string {
  const userNamespace = process.getuid === undefined
    ? "current-user"
    : `uid-${process.getuid()}`
  return join(parentRoot, `breadboard-sandbox-artifacts-${userNamespace}`)
}

const DEFAULT_SANDBOX_ARTIFACT_ROOT = canonicalSandboxArtifactRoot()
const LEGACY_SANDBOX_ARTIFACT_ROOT = join(tmpdir(), "breadboard-sandbox-artifacts")

function verifiedSandboxArtifactUri(
  artifactRoot: string,
  artifactName: string,
): string | null {
  try {
    const rootStat = lstatSync(artifactRoot)
    const destination = join(artifactRoot, artifactName)
    const artifactStat = lstatSync(destination)
    const currentUid = process.getuid?.()
    if (
      !rootStat.isDirectory()
      || rootStat.isSymbolicLink()
      || !artifactStat.isFile()
      || artifactStat.isSymbolicLink()
      || (currentUid !== undefined
        && (rootStat.uid !== currentUid
          || artifactStat.uid !== currentUid
          || (rootStat.mode & 0o077) !== 0
          || (artifactStat.mode & 0o077) !== 0))
    ) {
      return null
    }
    const digest = createHash("sha256").update(readFileSync(destination)).digest("hex")
    return digest === artifactName ? pathToFileURL(destination).href : null
  } catch {
    return null
  }
}

export function canonicalSandboxArtifactUri(
  ref: string,
  artifactRoot = DEFAULT_SANDBOX_ARTIFACT_ROOT,
): string {
  const match = /^sha256:([0-9a-f]{64})$/.exec(ref)
  if (!match) throw new Error("sandbox artifact ref must be a sha256 digest")
  const artifactName = match[1]!
  const directUri = verifiedSandboxArtifactUri(artifactRoot, artifactName)
  if (directUri !== null) {
    return directUri
  }
  if (artifactRoot !== DEFAULT_SANDBOX_ARTIFACT_ROOT) {
    const nestedUri = verifiedSandboxArtifactUri(
      canonicalSandboxArtifactRoot(artifactRoot),
      artifactName,
    )
    if (nestedUri !== null) {
      return nestedUri
    }
  } else {
    const legacyUri = verifiedSandboxArtifactUri(
      LEGACY_SANDBOX_ARTIFACT_ROOT,
      artifactName,
    )
    if (legacyUri !== null) {
      return legacyUri
    }
  }
  throw new Error("sandbox artifact has no verified content at its declared root")
}

export async function persistCanonicalSandboxArtifact(
  ref: string,
  content: string,
  artifactRoot = DEFAULT_SANDBOX_ARTIFACT_ROOT,
): Promise<string> {
  const expectedRef = `sha256:${createHash("sha256").update(content).digest("hex")}`
  if (ref !== expectedRef) {
    throw new Error("sandbox artifact content does not match its digest ref")
  }
  await mkdir(artifactRoot, { recursive: true, mode: 0o700 })
  const rootStat = await lstat(artifactRoot)
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    throw new Error("sandbox artifact root must be a directory, not a symbolic link")
  }
  const currentUid = process.getuid?.()
  if (currentUid !== undefined && rootStat.uid !== currentUid) {
    throw new Error("sandbox artifact root must be owned by the current user")
  }
  if (currentUid !== undefined && (rootStat.mode & 0o077) !== 0) {
    await chmod(artifactRoot, 0o700)
    const securedRootStat = await lstat(artifactRoot)
    if ((securedRootStat.mode & 0o077) !== 0) {
      throw new Error("sandbox artifact root must be accessible only by its owner")
    }
  }
  const destination = join(artifactRoot, ref.slice("sha256:".length))
  const temporary = `${destination}.${process.pid}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, content, {
      encoding: "utf8",
      flag: "wx",
      mode: 0o600,
    })
    await rename(temporary, destination)
  } finally {
    await rm(temporary, { force: true })
  }
  return canonicalSandboxArtifactUri(ref, artifactRoot)
}

export async function buildAndPersistCanonicalSandboxEvidence(input: {
  command: readonly string[]
  status: SandboxResultV1["status"]
  exitCode: number | null
  stdout: string
  stderr: string
  evidenceMode: SandboxRequestV1["evidence_mode"]
}, artifactRoot = DEFAULT_SANDBOX_ARTIFACT_ROOT): Promise<CanonicalSandboxEvidenceV1> {
  const { evidence, sideEffectManifest } = deriveCanonicalSandboxEvidence(input)
  await Promise.all([
    persistCanonicalSandboxArtifact(evidence.stdout_ref, input.stdout, artifactRoot),
    persistCanonicalSandboxArtifact(evidence.stderr_ref, input.stderr, artifactRoot),
    persistCanonicalSandboxArtifact(
      evidence.side_effect_digest,
      sideEffectManifest,
      artifactRoot,
    ),
  ])
  return evidence
}

export function buildExecutionDriverEvidenceExpectation(input: {
  capability: ExecutionCapabilityV1
  placementClass: ExecutionPlacementV1["placement_class"]
}): ExecutionDriverEvidenceExpectationV1 {
  const requireStrictEvidence = input.capability.evidence_mode === "replay_strict"
  const requireAuditedEvidence = input.capability.evidence_mode === "audit_full"
  const requiresSandboxRefs = input.placementClass !== "inline_ts"
  return {
    require_stdout_ref: requiresSandboxRefs,
    require_stderr_ref: requiresSandboxRefs,
    require_artifact_refs: requiresSandboxRefs,
    require_side_effect_digest: requiresSandboxRefs || requireStrictEvidence,
    require_evidence_refs: requireStrictEvidence || requireAuditedEvidence,
    require_usage_metadata: input.capability.evidence_mode !== "minimal",
  }
}

export function selectExecutionDriver(input: {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  drivers: ExecutionDriverV1[]
}): ExecutionDriverV1 | null {
  return (
    input.drivers.find((driver) => {
      if (!driver.supportedPlacements.includes(input.placement.placement_class)) {
        return false
      }
      return driver.supportsCapability(input.capability, input.placement.placement_class)
    }) ?? null
  )
}

export function buildPlannedExecution(input: {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  drivers: ExecutionDriverV1[]
  requestId: string
  command?: string[] | null
  workspaceRef?: string | null
  imageRef?: string | null
  metadata?: Record<string, unknown>
}): PlannedExecutionV1 | null {
  const driver = selectExecutionDriver({
    capability: input.capability,
    placement: input.placement,
    drivers: input.drivers,
  })
  if (!driver) {
    return null
  }
  if (input.command) {
    assertExecutionProgramAllowed(input.capability, input.command)
  }
  const sideEffectExpectation = buildExecutionDriverSideEffectExpectation(input.placement.placement_class)
  const evidenceExpectation = buildExecutionDriverEvidenceExpectation({
    capability: input.capability,
    placementClass: input.placement.placement_class,
  })
  const sandboxRequest =
    driver.buildSandboxRequest && input.command
      ? driver.buildSandboxRequest({
          requestId: input.requestId,
          capability: input.capability,
          placement: input.placement,
          command: input.command,
          workspaceRef: input.workspaceRef ?? null,
          imageRef: input.imageRef ?? null,
          metadata: input.metadata ?? {},
        })
      : null
  return {
    driver,
    capability: input.capability,
    placement: input.placement,
    sandboxRequest,
    sideEffectExpectation,
    evidenceExpectation,
  }
}
export * from "./world.js"
