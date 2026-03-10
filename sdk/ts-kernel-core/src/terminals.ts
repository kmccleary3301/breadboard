import {
  assertValid,
  type EffectiveToolSurfaceV1,
  type KernelEventV1,
  type TerminalCleanupResultV1,
  type TerminalOutputDeltaV1,
  type TerminalRegistrySnapshotV1,
  type TerminalSessionDescriptorV1,
  type TerminalSessionEndV1,
  type ToolBindingV1,
  type ToolSupportClaimV1,
} from "@breadboard/kernel-contracts"

export interface TerminalSessionSnapshot {
  readonly descriptor: TerminalSessionDescriptorV1
  readonly lifecycle: "running" | "ended"
  readonly recentOutputChunks: TerminalOutputDeltaV1[]
  readonly end?: TerminalSessionEndV1
}

export function reduceTerminalRegistry(events: readonly KernelEventV1[]): TerminalRegistrySnapshotV1 {
  const active = new Map<string, TerminalSessionSnapshot>()
  const ended: string[] = []

  for (const event of events) {
    if (event.kind === "terminal_session_begin") {
      const descriptor = assertValid<TerminalSessionDescriptorV1>(
        "terminalSessionDescriptor",
        event.payload,
      )
      active.set(descriptor.terminal_session_id, {
        descriptor,
        lifecycle: "running",
        recentOutputChunks: [],
      })
      continue
    }

    if (event.kind === "terminal_output_delta") {
      const delta = assertValid<TerminalOutputDeltaV1>("terminalOutputDelta", event.payload)
      const current = active.get(delta.terminal_session_id)
      if (current) {
        active.set(delta.terminal_session_id, {
          ...current,
          recentOutputChunks: [...current.recentOutputChunks.slice(-9), delta],
        })
      }
      continue
    }

    if (event.kind === "terminal_session_end") {
      const end = assertValid<TerminalSessionEndV1>("terminalSessionEnd", event.payload)
      active.delete(end.terminal_session_id)
      ended.push(end.terminal_session_id)
    }
  }

  return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", {
    schema_version: "bb.terminal_registry_snapshot.v1",
    snapshot_id: `term-reg:${events.length}`,
    active_sessions: [...active.values()].map((entry) => entry.descriptor),
    ended_session_ids: ended,
  })
}

export function buildTerminalCleanupResult(input: {
  cleanupId: string
  scope: TerminalCleanupResultV1["scope"]
  cleanedSessionIds: string[]
  failedSessionIds?: string[]
}): TerminalCleanupResultV1 {
  return assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
    schema_version: "bb.terminal_cleanup_result.v1",
    cleanup_id: input.cleanupId,
    scope: input.scope,
    cleaned_session_ids: input.cleanedSessionIds,
    failed_session_ids: input.failedSessionIds ?? [],
  })
}

export function buildEffectiveToolSurface(input: {
  surfaceId: string
  bindings: ToolBindingV1[]
  claims: ToolSupportClaimV1[]
  projectionProfileId?: string | null
}): EffectiveToolSurfaceV1 {
  const visibleClaims = input.claims.filter((claim) => claim.level !== "hidden")
  return assertValid<EffectiveToolSurfaceV1>("effectiveToolSurface", {
    schema_version: "bb.effective_tool_surface.v1",
    surface_id: input.surfaceId,
    tool_ids: visibleClaims.map((claim) => claim.tool_id),
    binding_ids: visibleClaims.map((claim) => claim.binding_id).filter(Boolean),
    hidden_tool_ids: input.claims.filter((claim) => claim.level === "hidden").map((claim) => claim.tool_id),
    projection_profile_id: input.projectionProfileId ?? null,
  })
}
