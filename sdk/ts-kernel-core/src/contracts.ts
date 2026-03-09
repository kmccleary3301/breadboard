import { createHash } from "node:crypto"

import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type RunContextV1,
  type RunRequestV1,
  type SessionTranscriptV1,
  type TranscriptContinuationPatchV1,
  type UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"

import type { StaticTextTurnOptions } from "./types.js"

export function buildRunContextFromRequest(
  requestInput: RunRequestV1,
  options: Omit<StaticTextTurnOptions, "assistantText" | "startedAt"> = {},
): RunContextV1 {
  const request = assertValid<RunRequestV1>("runRequest", requestInput)
  return {
    schema_version: "bb.run_context.v1",
    session_id: options.sessionId ?? `sess-${request.request_id}`,
    engine_family: options.engineFamily ?? "breadboard-ts",
    request_id: request.request_id,
    engine_ref: options.engineRef ?? "ts-kernel-core/v0",
    workspace_root: request.workspace_root ?? null,
    resolved_model: options.resolvedModel ?? request.requested_model ?? null,
    resolved_provider_route: options.resolvedProviderRoute ?? null,
    active_mode: options.activeMode ?? null,
    feature_flags: { ...(request.requested_features ?? {}) },
    execution_mode: options.executionMode ?? "static_text_turn",
    delegated_services: [],
    metadata: { source: "ts-kernel-core" },
  }
}

export function buildExecutionCapabilityFromRunRequest(
  requestInput: RunRequestV1,
  options: {
    capabilityId?: string
    securityTier?: ExecutionCapabilityV1["security_tier"]
    isolationClass?: ExecutionCapabilityV1["isolation_class"]
    secretMode?: ExecutionCapabilityV1["secret_mode"]
    evidenceMode?: ExecutionCapabilityV1["evidence_mode"]
    allowRunPrograms?: string[]
    allowReadPaths?: string[]
    allowWritePaths?: string[]
    allowNetHosts?: string[]
    ttyMode?: ExecutionCapabilityV1["tty_mode"]
  } = {},
): ExecutionCapabilityV1 {
  const request = assertValid<RunRequestV1>("runRequest", requestInput)
  return assertValid<ExecutionCapabilityV1>("executionCapability", {
    schema_version: "bb.execution_capability.v1",
    capability_id: options.capabilityId ?? `cap:${request.request_id}`,
    security_tier: options.securityTier ?? "trusted_dev",
    isolation_class: options.isolationClass ?? "none",
    allow_read_paths: options.allowReadPaths ?? (request.workspace_root ? [request.workspace_root] : []),
    allow_write_paths: options.allowWritePaths ?? (request.workspace_root ? [request.workspace_root] : []),
    allow_net_hosts: options.allowNetHosts ?? [],
    allow_run_programs: options.allowRunPrograms ?? [],
    allow_env_keys: [],
    secret_mode: options.secretMode ?? "ref_only",
    tty_mode: options.ttyMode ?? "optional",
    resource_budget: null,
    evidence_mode: options.evidenceMode ?? "replay_strict",
  })
}

export function buildExecutionPlacement(
  capability: ExecutionCapabilityV1,
  options: {
    placementId: string
    placementClass: ExecutionPlacementV1["placement_class"]
    runtimeId: string
    satisfiedSecurityTier?: string | null
    downgradeReason?: string | null
    metadata?: Record<string, unknown>
  },
): ExecutionPlacementV1 {
  return assertValid<ExecutionPlacementV1>("executionPlacement", {
    schema_version: "bb.execution_placement.v1",
    placement_id: options.placementId,
    placement_class: options.placementClass,
    runtime_id: options.runtimeId,
    capability_id: capability.capability_id,
    satisfied_security_tier: options.satisfiedSecurityTier ?? capability.security_tier,
    downgrade_reason: options.downgradeReason ?? null,
    metadata: options.metadata ?? {},
  })
}

export function buildTranscriptContinuationPatch(
  transcript: SessionTranscriptV1,
  options: {
    patchId: string
    preservedPrefixItems: number
  },
): TranscriptContinuationPatchV1 {
  const appendedItems = transcript.items.slice(options.preservedPrefixItems)
  const appendedMessages = appendedItems.map((item) => ({
    role:
      item.kind === "assistant_message"
        ? "assistant"
        : item.kind === "user_message"
          ? "user"
          : item.visibility === "model"
            ? "assistant"
            : "system",
    content: [item.content],
  }))
  const postStateDigest = `sha256:${createHash("sha256")
    .update(JSON.stringify(transcript.items))
    .digest("hex")}`
  return assertValid<TranscriptContinuationPatchV1>("transcriptContinuationPatch", {
    schema_version: "bb.transcript_continuation_patch.v1",
    patch_id: options.patchId,
    pre_state_ref: `transcript://${transcript.sessionId}@prefix:${options.preservedPrefixItems}`,
    appended_messages: appendedMessages,
    appended_tool_events: [],
    lineage_updates: [],
    compaction_markers: [],
    post_state_digest: postStateDigest,
    lossiness_flags: [],
  })
}

export function buildUnsupportedCase(
  reasonCode: string,
  summary: string,
  options: {
    contractFamily?: string
    fallbackAllowed?: boolean
    fallbackTaken?: boolean
    requiredCapabilityId?: string | null
    unavailablePlacement?: string | null
    evidenceRefs?: string[]
    metadata?: Record<string, unknown>
  } = { contractFamily: "bb.unsupported_case.v1" },
): UnsupportedCaseV1 {
  return assertValid<UnsupportedCaseV1>("unsupportedCase", {
    schema_version: "bb.unsupported_case.v1",
    reason_code: reasonCode,
    summary,
    contract_family: options.contractFamily,
    fallback_allowed: options.fallbackAllowed ?? false,
    fallback_taken: options.fallbackTaken ?? false,
    required_capability_id: options.requiredCapabilityId ?? null,
    unavailable_placement: options.unavailablePlacement ?? null,
    evidence_refs: options.evidenceRefs ?? [],
    metadata: options.metadata ?? {},
  })
}
