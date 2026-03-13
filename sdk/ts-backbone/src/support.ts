import type {
  CoordinationInspectionSnapshotV1,
  CoordinationInterventionSnapshotV1,
  DirectiveCodeV1,
  DirectiveV1,
  ExecutionCapabilityV1,
  KernelEventV1,
  ReviewVerdictV1,
  RunRequestV1,
  SignalCodeV1,
  SignalV1,
} from "@breadboard/kernel-contracts"
import { buildExecutionCapabilityFromRunRequest } from "@breadboard/kernel-core"
import type {
  BackboneTurnResult,
  ProjectionProfile,
  ProjectionProfileId,
  SupportClaim,
  ToolTurnInput,
} from "./types.js"
import type { ExecutionProfileId, Workspace } from "@breadboard/workspace"

const COORDINATION_SIGNAL_KINDS = new Set(["coordination.signal", "coordination_signal"])
const COORDINATION_REVIEW_KINDS = new Set(["coordination.review_verdict", "coordination_review_verdict"])
const COORDINATION_DIRECTIVE_KINDS = new Set(["coordination.directive", "coordination_directive"])

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null
  }
  return value as Record<string, unknown>
}

function cloneJsonLike<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function asSignal(value: unknown): SignalV1 | null {
  const record = asRecord(value)
  if (!record) return null
  if (typeof record.signal_id !== "string" || record.signal_id.trim().length === 0) return null
  if (typeof record.code !== "string" || record.code.trim().length === 0) return null
  return cloneJsonLike(record as unknown as SignalV1)
}

function asReviewVerdict(value: unknown): ReviewVerdictV1 | null {
  const record = asRecord(value)
  if (!record) return null
  if (typeof record.verdict_id !== "string" || record.verdict_id.trim().length === 0) return null
  if (typeof record.verdict_code !== "string" || record.verdict_code.trim().length === 0) return null
  return cloneJsonLike(record as unknown as ReviewVerdictV1)
}

function asDirective(value: unknown): DirectiveV1 | null {
  const record = asRecord(value)
  if (!record) return null
  if (typeof record.directive_id !== "string" || record.directive_id.trim().length === 0) return null
  if (typeof record.directive_code !== "string" || record.directive_code.trim().length === 0) return null
  return cloneJsonLike(record as unknown as DirectiveV1)
}

function normalizeAllowedHostActions(value: unknown): DirectiveCodeV1[] | undefined {
  if (!Array.isArray(value)) {
    return undefined
  }
  const actions = value
    .filter((item): item is DirectiveCodeV1 => typeof item === "string" && item.trim().length > 0)
    .map((item) => item as DirectiveCodeV1)
  return actions.length > 0 ? actions : undefined
}

function buildInterventionSnapshots(options: {
  signals: readonly SignalV1[]
  reviewVerdicts: readonly ReviewVerdictV1[]
  directives: readonly DirectiveV1[]
  resolved: boolean
}): CoordinationInterventionSnapshotV1[] {
  const signalById = new Map<string, SignalV1>()
  for (const signal of options.signals) {
    signalById.set(signal.signal_id, cloneJsonLike(signal))
  }

  const directivesByVerdict = new Map<string, DirectiveV1[]>()
  const hostDirectivesByVerdict = new Map<string, DirectiveV1[]>()
  for (const directive of options.directives) {
    const verdictId = directive.based_on_verdict_id?.trim()
    if (!verdictId) continue
    const directiveClone = cloneJsonLike(directive)
    directivesByVerdict.set(verdictId, [...(directivesByVerdict.get(verdictId) ?? []), directiveClone])
    if (directive.issuer_role === "host") {
      hostDirectivesByVerdict.set(verdictId, [...(hostDirectivesByVerdict.get(verdictId) ?? []), directiveClone])
    }
  }

  const snapshots: CoordinationInterventionSnapshotV1[] = []
  for (const verdict of options.reviewVerdicts) {
    if (verdict.verdict_code !== "human_required") continue
    const verdictId = verdict.verdict_id?.trim()
    if (!verdictId) continue
    const hostResponses = hostDirectivesByVerdict.get(verdictId) ?? []
    const isResolved = hostResponses.length > 0
    if (isResolved !== options.resolved) continue
    const signal = signalById.get(verdict.subject.signal_id) ?? null
    const payload = asRecord(signal?.payload) ?? {}
    const metadata = asRecord(verdict.metadata) ?? {}
    const blockingReason = typeof verdict.blocking_reason === "string" && verdict.blocking_reason.trim().length > 0
      ? verdict.blocking_reason
      : typeof payload.blocking_reason === "string" && payload.blocking_reason.trim().length > 0
        ? payload.blocking_reason
        : null
    const requiredInput = typeof payload.required_input === "string" && payload.required_input.trim().length > 0
      ? payload.required_input
      : null
    snapshots.push({
      intervention_id: `intervention_${verdictId}`,
      status: isResolved ? "resolved" : "pending",
      review_verdict_id: verdictId,
      signal_id: verdict.subject.signal_id,
      source_task_id: verdict.subject.source_task_id,
      mission_task_id: verdict.subject.mission_task_id ?? null,
      required_input: requiredInput,
      blocking_reason: blockingReason,
      allowed_host_actions: normalizeAllowedHostActions(metadata.allowed_host_actions ?? payload.allowed_host_actions),
      review_verdict: cloneJsonLike(verdict),
      signal: signal ? cloneJsonLike(signal) : null,
      directives: (directivesByVerdict.get(verdictId) ?? []).map((item) => cloneJsonLike(item)),
      host_responses: hostResponses.map((item) => cloneJsonLike(item)),
    })
  }
  return snapshots
}

export function buildCoordinationInspectionSnapshot(input: {
  snapshot?: CoordinationInspectionSnapshotV1 | null
  events?: readonly KernelEventV1[]
} = {}): CoordinationInspectionSnapshotV1 {
  if (input.snapshot) {
    return cloneJsonLike(input.snapshot)
  }

  const signals: SignalV1[] = []
  const reviewVerdicts: ReviewVerdictV1[] = []
  const directives: DirectiveV1[] = []

  for (const event of input.events ?? []) {
    const kind = String(event.kind ?? "").trim()
    if (COORDINATION_SIGNAL_KINDS.has(kind)) {
      const signal = asSignal(event.payload)
      if (signal) signals.push(signal)
      continue
    }
    if (COORDINATION_REVIEW_KINDS.has(kind)) {
      const verdict = asReviewVerdict(event.payload)
      if (verdict) reviewVerdicts.push(verdict)
      continue
    }
    if (COORDINATION_DIRECTIVE_KINDS.has(kind)) {
      const directive = asDirective(event.payload)
      if (directive) directives.push(directive)
    }
  }

  const latestSignalByCode: Partial<Record<SignalCodeV1, SignalV1>> = {}
  for (const signal of signals) {
    latestSignalByCode[signal.code] = cloneJsonLike(signal)
  }

  return {
    signals: signals.map((item) => cloneJsonLike(item)),
    review_verdicts: reviewVerdicts.map((item) => cloneJsonLike(item)),
    directives: directives.map((item) => cloneJsonLike(item)),
    latest_signal_by_code: latestSignalByCode,
    unresolved_interventions: buildInterventionSnapshots({
      signals,
      reviewVerdicts,
      directives,
      resolved: false,
    }),
    resolved_interventions: buildInterventionSnapshots({
      signals,
      reviewVerdicts,
      directives,
      resolved: true,
    }),
  }
}

export function buildProjectionProfile(id: ProjectionProfileId): ProjectionProfile {
  switch (id) {
    case "host_callbacks":
      return { id, summary: "Project turns for host callback/event consumers." }
    case "raw_kernel_events":
      return { id, summary: "Expose raw kernel events without additional host shaping." }
    case "ai_sdk_transport":
      return { id, summary: "Project turns for AI SDK-compatible transport streams." }
  }
}

export function buildExecutionProfileIdFromCapability(capability: ExecutionCapabilityV1): ExecutionProfileId {
  if (capability.security_tier === "multi_tenant") return "remote_isolated"
  if (["oci", "gvisor", "kata"].includes(capability.isolation_class)) return "sandboxed_local"
  if (capability.security_tier === "shared_host") return "constrained_local"
  return "trusted_local"
}

export function buildSupportClaim(options: {
  workspace: Workspace
  request: RunRequestV1
  executionProfileId?: ExecutionProfileId
  unsupportedFields?: readonly string[]
  summary?: string
  recommendedHostMode?: "inline" | "streaming" | "background"
  confidence?: "high" | "medium" | "low"
}): SupportClaim {
  const capability = buildExecutionCapabilityFromRunRequest(options.request, {
    capabilityId: `${options.request.request_id}:backbone-support`,
  })
  const executionProfileId = options.executionProfileId ?? buildExecutionProfileIdFromCapability(capability)
  const executionProfile = options.workspace.getExecutionProfile(executionProfileId)
  const unsupportedFields = [...(options.unsupportedFields ?? [])]
  const supported = options.workspace.supportsProfile(executionProfileId) && unsupportedFields.length === 0
  return {
    level: supported ? "supported" : "unsupported",
    summary:
      options.summary ??
      (supported
        ? `Supported on execution profile ${executionProfileId}.`
        : `Unsupported on execution profile ${executionProfileId}.`),
    executionProfileId,
    executionProfile,
    fallbackAvailable: !supported,
    unsupportedFields,
    evidenceMode: capability.evidence_mode,
    recommendedHostMode:
      options.recommendedHostMode ??
      (executionProfile.backendHint === "remote" ? "background" : capability.isolation_class === "none" ? "inline" : "streaming"),
    confidence: options.confidence ?? (supported ? "high" : "medium"),
  }
}

export function buildToolTurnSupportClaim(workspace: Workspace, input: ToolTurnInput): SupportClaim {
  const executionProfileId: ExecutionProfileId =
    input.driverIdHint === "remote"
      ? "remote_isolated"
      : input.driverIdHint === "oci"
        ? "sandboxed_local"
        : "trusted_local"
  return buildSupportClaim({
    workspace,
    request: input.request,
    executionProfileId,
    summary: `Tool turn requested on ${executionProfileId}.`,
    recommendedHostMode: executionProfileId === "remote_isolated" ? "background" : "streaming",
  })
}

export function buildBackboneTurnResult(input: {
  supportClaim: SupportClaim
  projectionProfile: ProjectionProfile
  transcript: BackboneTurnResult["transcript"]
  events: BackboneTurnResult["events"]
  runContextId: string
  providerTurn?: BackboneTurnResult["providerTurn"]
  driverTurn?: BackboneTurnResult["driverTurn"]
  unsupportedCase?: BackboneTurnResult["unsupportedCase"]
}): BackboneTurnResult {
  return {
    supportClaim: input.supportClaim,
    projectionProfile: input.projectionProfile,
    transcript: input.transcript,
    events: input.events,
    coordinationInspection: buildCoordinationInspectionSnapshot({ events: input.events }),
    runContextId: input.runContextId,
    providerTurn: input.providerTurn,
    driverTurn: input.driverTurn,
    unsupportedCase: input.unsupportedCase,
  }
}
