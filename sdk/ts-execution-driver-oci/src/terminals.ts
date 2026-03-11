import { randomUUID } from "node:crypto"

import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type TerminalCleanupResultV1,
  type TerminalInteractionV1,
  type TerminalOutputDeltaV1,
  type TerminalRegistrySnapshotV1,
  type TerminalSessionDescriptorV1,
  type TerminalSessionEndV1,
} from "@breadboard/kernel-contracts"
import type {
  TerminalSessionCleanupInputV1,
  TerminalSessionDriverV1,
  TerminalSessionInteractionInputV1,
  TerminalSessionInteractionResultV1,
  TerminalSessionStartInputV1,
  TerminalSessionStartResultV1,
} from "@breadboard/execution-drivers"

export interface OciTerminalSessionAdapterStartInput {
  readonly descriptor: TerminalSessionDescriptorV1
}

export interface OciTerminalSessionAdapterInteractionInput {
  readonly descriptor: TerminalSessionDescriptorV1
  readonly interaction: TerminalInteractionV1
}

export interface OciTerminalSessionAdapterCleanupInput {
  readonly sessionIds: readonly string[]
  readonly signal: string
}

export interface OciTerminalSessionAdapterResult {
  readonly outputDeltas?: readonly TerminalOutputDeltaV1[]
  readonly end?: TerminalSessionEndV1
}

export interface OciTerminalSessionAdapter {
  startSession(input: OciTerminalSessionAdapterStartInput): Promise<OciTerminalSessionAdapterResult>
  interactSession(input: OciTerminalSessionAdapterInteractionInput): Promise<OciTerminalSessionAdapterResult>
  cleanupSessions?(input: OciTerminalSessionAdapterCleanupInput): Promise<readonly string[]>
}

interface OciTerminalSessionRecord {
  readonly descriptor: TerminalSessionDescriptorV1
  end: TerminalSessionEndV1 | null
}

function normalizeSignal(signal?: string | null): string {
  return signal ?? "SIGTERM"
}

function buildDescriptor(input: TerminalSessionStartInputV1): TerminalSessionDescriptorV1 {
  return assertValid<TerminalSessionDescriptorV1>("terminalSessionDescriptor", {
    schema_version: "bb.terminal_session_descriptor.v1",
    terminal_session_id: input.terminalSessionId,
    startup_call_id: input.startupCallId ?? null,
    owner_task_id: input.ownerTaskId ?? null,
    public_handles:
      input.publicHandles ??
      [{ namespace: "breadboard", label: "session_id", value: input.terminalSessionId, audience: "host" }],
    command: input.command,
    cwd: input.cwd ?? null,
    stream_mode: input.streamMode ?? "pipes",
    stream_split: input.streamSplit ?? "stdout_stderr",
    capability_id: input.capability?.capability_id ?? null,
    placement_id: input.placement?.placement_id ?? null,
    persistence_scope: input.persistenceScope ?? "thread",
    continuation_scope: input.continuationScope ?? "both",
  })
}

function buildInteraction(
  descriptor: TerminalSessionDescriptorV1,
  input: TerminalSessionInteractionInputV1,
): TerminalInteractionV1 {
  return assertValid<TerminalInteractionV1>("terminalInteraction", {
    schema_version: "bb.terminal_interaction.v1",
    terminal_session_id: descriptor.terminal_session_id,
    startup_call_id: descriptor.startup_call_id ?? null,
    causing_call_id: input.causingCallId ?? null,
    interaction_kind: input.interactionKind,
    input_b64: input.interactionKind === "stdin" ? input.inputB64 ?? null : null,
    signal: input.interactionKind === "signal" ? normalizeSignal(input.signal) : null,
  })
}

export class OciTerminalSessionManager {
  private readonly sessions = new Map<string, OciTerminalSessionRecord>()
  private readonly endedSessionIds: string[] = []

  private rememberEndedSession(sessionId: string): void {
    const existingIndex = this.endedSessionIds.indexOf(sessionId)
    if (existingIndex >= 0) {
      this.endedSessionIds.splice(existingIndex, 1)
    }
    this.endedSessionIds.push(sessionId)
    if (this.endedSessionIds.length > 32) {
      this.endedSessionIds.splice(0, this.endedSessionIds.length - 32)
    }
  }

  constructor(private readonly adapter: OciTerminalSessionAdapter) {}

  async startSession(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1> {
    const descriptor = buildDescriptor(input)
    const result = await this.adapter.startSession({ descriptor })
    this.sessions.set(descriptor.terminal_session_id, {
      descriptor,
      end: result.end ?? null,
    })
    if (result.end) {
      this.sessions.delete(descriptor.terminal_session_id)
      this.rememberEndedSession(descriptor.terminal_session_id)
    }
    return {
      descriptor,
      outputDeltas: [...(result.outputDeltas ?? [])],
      end: result.end,
    }
  }

  async interactSession(input: TerminalSessionInteractionInputV1): Promise<TerminalSessionInteractionResultV1> {
    const record = this.sessions.get(input.terminalSessionId)
    if (!record) {
      if (this.endedSessionIds.includes(input.terminalSessionId)) {
        throw new Error(`OCI terminal session already ended: ${input.terminalSessionId}`)
      }
      throw new Error(`Unknown OCI terminal session: ${input.terminalSessionId}`)
    }
    const interaction = buildInteraction(record.descriptor, input)
    const result = await this.adapter.interactSession({
      descriptor: record.descriptor,
      interaction,
    })
    if (result.end) {
      this.sessions.delete(record.descriptor.terminal_session_id)
      this.rememberEndedSession(record.descriptor.terminal_session_id)
    }
    return {
      interaction,
      outputDeltas: [...(result.outputDeltas ?? [])],
      end: result.end,
    }
  }

  async snapshotRegistry(): Promise<TerminalRegistrySnapshotV1> {
    return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", {
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: `oci-term-reg:${Date.now()}`,
      active_sessions: [...this.sessions.values()].map((record) => record.descriptor),
      ended_session_ids: [...this.endedSessionIds],
    })
  }

  async cleanupSessions(input: TerminalSessionCleanupInputV1): Promise<TerminalCleanupResultV1> {
    const targetIds =
      input.scope === "single"
        ? input.sessionIds?.slice(0, 1) ?? []
        : input.scope === "filtered"
          ? input.sessionIds ?? []
          : [...this.sessions.keys()]
    const alreadyEnded = targetIds.filter((sessionId) => !this.sessions.has(sessionId) && this.endedSessionIds.includes(sessionId))
    const cleaned = this.adapter.cleanupSessions
      ? [...await this.adapter.cleanupSessions({ sessionIds: targetIds, signal: normalizeSignal(input.signal) })]
      : [...targetIds]
    const cleanedSet = new Set([...cleaned, ...alreadyEnded])
    const failed = targetIds.filter((sessionId) => !cleanedSet.has(sessionId))
    for (const sessionId of cleanedSet) {
      this.sessions.delete(sessionId)
      this.rememberEndedSession(sessionId)
    }
    return assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId || randomUUID(),
      scope: input.scope,
      cleaned_session_ids: [...cleanedSet],
      failed_session_ids: failed,
      metadata: { signal: normalizeSignal(input.signal) },
    })
  }
}

export function makeOciTerminalSessionDriver(
  adapter: OciTerminalSessionAdapter,
): Pick<
  TerminalSessionDriverV1,
  "supportsTerminalSessions" | "startTerminalSession" | "interactTerminalSession" | "snapshotTerminalRegistry" | "cleanupTerminalSessions"
> {
  const manager = new OciTerminalSessionManager(adapter)
  return {
    supportsTerminalSessions(
      capability: ExecutionCapabilityV1,
      placementClass: ExecutionPlacementV1["placement_class"],
    ): boolean {
      return (
        ["local_oci", "local_oci_gvisor", "local_oci_kata"].includes(placementClass) &&
        ["oci", "gvisor", "kata"].includes(capability.isolation_class)
      )
    },
    startTerminalSession(input) {
      return manager.startSession(input)
    },
    interactTerminalSession(input) {
      return manager.interactSession(input)
    },
    snapshotTerminalRegistry() {
      return manager.snapshotRegistry()
    },
    cleanupTerminalSessions(input) {
      return manager.cleanupSessions(input)
    },
  }
}
