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
  readonly timeoutMs?: number
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
  const abortController =
    options.timeoutMs != null && options.signal == null ? new AbortController() : null
  let timeoutHandle: ReturnType<typeof setTimeout> | undefined
  try {
    if (abortController) {
      timeoutHandle = setTimeout(() => {
        abortController.abort(new Error(`Remote terminal execution timed out after ${options.timeoutMs}ms`))
      }, options.timeoutMs)
    }
    const response = await fetchImpl(options.endpointUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(options.headers ?? {}),
      },
      body: JSON.stringify(envelope),
      signal: options.signal ?? abortController?.signal,
    })
    const payload = (await response.json()) as RemoteTerminalSessionResponseEnvelopeV1 & {
      readonly error?: { readonly summary?: string; readonly message?: string }
      readonly metadata?: { readonly summary?: string }
    }
    if (!response.ok) {
      const summary =
        payload.error?.summary ??
        payload.error?.message ??
        payload.metadata?.summary ??
        `Remote terminal endpoint returned HTTP ${response.status}`
      throw new Error(summary)
    }
    return payload
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error(`Remote terminal execution timed out after ${options.timeoutMs ?? "unknown"}ms`)
    }
    if (error instanceof Error && /timed out after/.test(error.message)) {
      throw new Error(error.message)
    }
    throw error
  } finally {
    if (timeoutHandle) {
      clearTimeout(timeoutHandle)
    }
  }
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
  }

  async startSession(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1> {
    if (this.sessions.has(input.terminalSessionId)) {
      throw new Error(`Remote terminal session already active: ${input.terminalSessionId}`)
    }
    const descriptor = buildDescriptor(input)
    const response = await executeRemoteTerminalRequest(this.httpOptions, {
      schema_version: "bb.remote_terminal_request.v1",
      action: "start",
      payload: { descriptor },
      metadata: this.httpOptions.metadata ?? {},
    })
    const outputDeltas = (response.payload.output_deltas as TerminalSessionStartResultV1["outputDeltas"] | undefined) ?? []
    const end = (response.payload.end as TerminalSessionEndV1 | undefined) ?? undefined
    if (outputDeltas) {
      for (const delta of outputDeltas) {
        if (delta.terminal_session_id !== descriptor.terminal_session_id) {
          throw new Error(
            `Terminal output delta session ID '${delta.terminal_session_id}' does not match session ID '${descriptor.terminal_session_id}'`,
          )
        }
      }
    }
    if (end) {
      if (end.terminal_session_id !== descriptor.terminal_session_id) {
        throw new Error(
          `Terminal session end session ID '${end.terminal_session_id}' does not match session ID '${descriptor.terminal_session_id}'`,
        )
      }
    }
    if (!end) {
      const endedIdx = this.endedSessionIds.indexOf(descriptor.terminal_session_id)
      if (endedIdx >= 0) {
        this.endedSessionIds.splice(endedIdx, 1)
      }
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
      if (this.endedSessionIds.includes(input.terminalSessionId)) {
        throw new Error(`Remote terminal session already ended: ${input.terminalSessionId}`)
      }
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
    if (outputDeltas) {
      for (const delta of outputDeltas) {
        if (delta.terminal_session_id !== record.descriptor.terminal_session_id) {
          throw new Error(
            `Terminal output delta session ID '${delta.terminal_session_id}' does not match interaction session ID '${record.descriptor.terminal_session_id}'`,
          )
        }
      }
    }
    if (end) {
      if (end.terminal_session_id !== record.descriptor.terminal_session_id) {
        throw new Error(
          `Terminal session end session ID '${end.terminal_session_id}' does not match interaction session ID '${record.descriptor.terminal_session_id}'`,
        )
      }
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
    const rawSnapshot = response.payload.snapshot as TerminalRegistrySnapshotV1 | undefined
    if (rawSnapshot) {
      const snapshot = assertValid<TerminalRegistrySnapshotV1>(
        "terminalRegistrySnapshot",
        rawSnapshot,
      )
      const activeSet = new Set(
        snapshot.active_sessions.map((session) => session.terminal_session_id),
      )
      const sanitizedEnded = (snapshot.ended_session_ids ?? []).filter(
        (sessionId) => !activeSet.has(sessionId),
      )
      for (const descriptor of snapshot.active_sessions) {
        this.sessions.set(descriptor.terminal_session_id, { descriptor })
        const endedIndex = this.endedSessionIds.indexOf(descriptor.terminal_session_id)
        if (endedIndex >= 0) this.endedSessionIds.splice(endedIndex, 1)
      }
      for (const sessionId of sanitizedEnded) {
        this.sessions.delete(sessionId)
        this.rememberEndedSession(sessionId)
      }
      return {
        ...snapshot,
        ended_session_ids: sanitizedEnded,
      }
    }
    const activeIds = new Set(this.sessions.keys())
    return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", {
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: `remote-term-reg:${Date.now()}`,
      active_sessions: [...this.sessions.values()].map((record) => record.descriptor),
      ended_session_ids: this.endedSessionIds.filter((id) => !activeIds.has(id)),
    })
  }

  async cleanupSessions(input: TerminalSessionCleanupInputV1): Promise<TerminalCleanupResultV1> {
    const targetIds =
      input.scope === "single"
        ? input.sessionIds?.slice(0, 1) ?? []
        : input.scope === "filtered"
          ? input.sessionIds ?? []
          : [...new Set([...this.sessions.keys(), ...this.endedSessionIds])]
    const targetSet = new Set(targetIds)
    const alreadyEnded = targetIds.filter((sessionId) => !this.sessions.has(sessionId) && this.endedSessionIds.includes(sessionId))
    const response = await executeRemoteTerminalRequest(this.httpOptions, {
      schema_version: "bb.remote_terminal_request.v1",
      action: "cleanup",
      payload: { session_ids: targetIds, signal: normalizeSignal(input.signal) },
      metadata: this.httpOptions.metadata ?? {},
    })
    const rawCleaned = response.payload.cleaned_session_ids
    const rawFailed = response.payload.failed_session_ids

    const parseStringArray = (val: unknown): string[] | null => {
      if (val === undefined || val === null) return null
      if (!Array.isArray(val)) return null
      if (!val.every((item) => typeof item === "string")) return null
      return val
    }

    const validatedCleaned = parseStringArray(rawCleaned)
    const validatedFailed = parseStringArray(rawFailed)

    if (rawCleaned !== undefined && rawCleaned !== null && validatedCleaned === null) {
      throw new Error("Remote cleanup returned malformed cleaned_session_ids (expected array of strings)")
    }
    if (rawFailed !== undefined && rawFailed !== null && validatedFailed === null) {
      throw new Error("Remote cleanup returned malformed failed_session_ids (expected array of strings)")
    }
    const failedRaw = validatedFailed ?? []
    const failedSet = new Set(failedRaw)
    const cleanedRaw =
      validatedCleaned ??
      (validatedFailed ? targetIds.filter((id) => !failedSet.has(id)) : targetIds)

    const cleanedTargeted = cleanedRaw.filter((id) => targetSet.has(id))
    const failedTargeted = failedRaw.filter((id) => targetSet.has(id))

    // Validate disjointness only for the requested target set.
    const cleanedSetCheck = new Set(cleanedTargeted)
    for (const failedId of failedTargeted) {
      if (cleanedSetCheck.has(failedId)) {
        throw new Error(`Invalid cleanup result: session ID '${failedId}' appears in both cleaned_session_ids and failed_session_ids`)
      }
    }
    const cleanedSessionIds = [
      ...new Set([...cleanedTargeted, ...alreadyEnded]),
    ]
    const finalFailed = Array.from(new Set([...failedTargeted, ...targetIds.filter((sessionId) => !cleanedSessionIds.includes(sessionId))]))
    for (const sessionId of cleanedSessionIds) {
      this.sessions.delete(sessionId)
      this.rememberEndedSession(sessionId)
    }
    return assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId || randomUUID(),
      scope: input.scope,
      cleaned_session_ids: cleanedSessionIds,
      failed_session_ids: finalFailed,
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
