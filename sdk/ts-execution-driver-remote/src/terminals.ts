import { randomUUID } from "node:crypto"

import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type TerminalCleanupResultV1,
  type TerminalInteractionV1,
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

export interface RemoteTerminalExecutionHttpOptions {
  readonly endpointUrl: string
  readonly headers?: Record<string, string>
  readonly fetchImpl?: typeof fetch
  readonly metadata?: Record<string, unknown>
  readonly signal?: AbortSignal
}

export interface RemoteTerminalSessionRequestEnvelopeV1 {
  readonly schema_version: "bb.remote_terminal_request.v1"
  readonly action: "start" | "interact" | "snapshot" | "cleanup"
  readonly payload: Record<string, unknown>
  readonly metadata?: Record<string, unknown>
}

export interface RemoteTerminalSessionResponseEnvelopeV1 {
  readonly schema_version: "bb.remote_terminal_response.v1"
  readonly payload: Record<string, unknown>
  readonly metadata?: Record<string, unknown>
}

interface RemoteTerminalSessionRecord {
  readonly descriptor: TerminalSessionDescriptorV1
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

async function executeRemoteTerminalRequest(
  options: RemoteTerminalExecutionHttpOptions,
  envelope: RemoteTerminalSessionRequestEnvelopeV1,
): Promise<RemoteTerminalSessionResponseEnvelopeV1> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch
  if (typeof fetchImpl !== "function") {
    throw new Error("Remote terminal execution requires a fetch-compatible implementation")
  }
  const response = await fetchImpl(options.endpointUrl, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(options.headers ?? {}),
    },
    body: JSON.stringify(envelope),
    signal: options.signal,
  })
  const payload = (await response.json()) as RemoteTerminalSessionResponseEnvelopeV1
  if (!response.ok) {
    throw new Error(`Remote terminal endpoint returned HTTP ${response.status}`)
  }
  return payload
}

export class RemoteTerminalSessionManager {
  private readonly sessions = new Map<string, RemoteTerminalSessionRecord>()
  private readonly endedSessionIds: string[] = []

  constructor(private readonly httpOptions: RemoteTerminalExecutionHttpOptions) {}

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

  async startSession(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1> {
    const descriptor = buildDescriptor(input)
    const response = await executeRemoteTerminalRequest(this.httpOptions, {
      schema_version: "bb.remote_terminal_request.v1",
      action: "start",
      payload: { descriptor },
      metadata: this.httpOptions.metadata ?? {},
    })
    const outputDeltas = (response.payload.output_deltas as TerminalSessionStartResultV1["outputDeltas"] | undefined) ?? []
    const end = (response.payload.end as TerminalSessionEndV1 | undefined) ?? undefined
    if (!end) {
      this.sessions.set(descriptor.terminal_session_id, { descriptor })
    } else {
      this.rememberEndedSession(descriptor.terminal_session_id)
    }
    return {
      descriptor,
      outputDeltas,
      end,
    }
  }

  async interactSession(input: TerminalSessionInteractionInputV1): Promise<TerminalSessionInteractionResultV1> {
    const record = this.sessions.get(input.terminalSessionId)
    if (!record) {
      throw new Error(`Unknown remote terminal session: ${input.terminalSessionId}`)
    }
    const interaction = buildInteraction(record.descriptor, input)
    const response = await executeRemoteTerminalRequest(this.httpOptions, {
      schema_version: "bb.remote_terminal_request.v1",
      action: "interact",
      payload: { descriptor: record.descriptor, interaction },
      metadata: this.httpOptions.metadata ?? {},
    })
    const outputDeltas = (response.payload.output_deltas as TerminalSessionInteractionResultV1["outputDeltas"] | undefined) ?? []
    const end = (response.payload.end as TerminalSessionEndV1 | undefined) ?? undefined
    if (end) {
      this.sessions.delete(record.descriptor.terminal_session_id)
      this.rememberEndedSession(record.descriptor.terminal_session_id)
    }
    return {
      interaction,
      outputDeltas,
      end,
    }
  }

  async snapshotRegistry(): Promise<TerminalRegistrySnapshotV1> {
    const response = await executeRemoteTerminalRequest(this.httpOptions, {
      schema_version: "bb.remote_terminal_request.v1",
      action: "snapshot",
      payload: {},
      metadata: this.httpOptions.metadata ?? {},
    })
    const snapshot = response.payload.snapshot as TerminalRegistrySnapshotV1 | undefined
    if (snapshot) {
      for (const sessionId of snapshot.ended_session_ids ?? []) {
        this.rememberEndedSession(sessionId)
      }
      return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", snapshot)
    }
    return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", {
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: `remote-term-reg:${Date.now()}`,
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
    const response = await executeRemoteTerminalRequest(this.httpOptions, {
      schema_version: "bb.remote_terminal_request.v1",
      action: "cleanup",
      payload: { session_ids: targetIds, signal: normalizeSignal(input.signal) },
      metadata: this.httpOptions.metadata ?? {},
    })
    const cleanedSessionIds = (response.payload.cleaned_session_ids as string[] | undefined) ?? targetIds
    const failedSessionIds = (response.payload.failed_session_ids as string[] | undefined) ?? []
    for (const sessionId of cleanedSessionIds) {
      this.sessions.delete(sessionId)
      this.rememberEndedSession(sessionId)
    }
    return assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId || randomUUID(),
      scope: input.scope,
      cleaned_session_ids: cleanedSessionIds,
      failed_session_ids: failedSessionIds,
      metadata: { signal: normalizeSignal(input.signal) },
    })
  }
}

export function makeRemoteTerminalSessionDriver(
  httpOptions: RemoteTerminalExecutionHttpOptions,
): Pick<
  TerminalSessionDriverV1,
  "supportsTerminalSessions" | "startTerminalSession" | "interactTerminalSession" | "snapshotTerminalRegistry" | "cleanupTerminalSessions"
> {
  const manager = new RemoteTerminalSessionManager(httpOptions)
  return {
    supportsTerminalSessions(
      capability: ExecutionCapabilityV1,
      placementClass: ExecutionPlacementV1["placement_class"],
    ): boolean {
      return (
        ["remote_worker", "delegated_python", "delegated_oci", "delegated_microvm"].includes(placementClass) &&
        ["remote_service", "microvm", "oci", "gvisor", "kata"].includes(capability.isolation_class)
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
