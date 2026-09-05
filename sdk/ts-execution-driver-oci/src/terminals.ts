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
import { assertExecutionProgramAllowed } from "@breadboard/execution-drivers"

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
  cleanupSessions?(
    input: OciTerminalSessionAdapterCleanupInput,
  ): Promise<readonly string[] | { readonly cleaned_session_ids?: readonly string[]; readonly failed_session_ids?: readonly string[] }>
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
  }

  constructor(
    private readonly adapter: OciTerminalSessionAdapter,
    private readonly terminationGraceMs: number = 2000,
  ) {}
  async startSession(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1> {
    if (input.capability) {
      assertExecutionProgramAllowed(input.capability, input.command)
    }
    if (typeof this.adapter.cleanupSessions !== "function") {
      throw new Error("OCI terminal adapter must support cleanupSessions before launching terminal sessions")
    }
    if (this.sessions.has(input.terminalSessionId)) {
      throw new Error(`OCI terminal session already active: ${input.terminalSessionId}`)
    }
    const descriptor = buildDescriptor(input)
    const result = await this.adapter.startSession({ descriptor })
    try {
      if (result.outputDeltas) {
        for (const delta of result.outputDeltas) {
          if (delta.terminal_session_id !== descriptor.terminal_session_id) {
            throw new Error(
              `Terminal output delta session ID '${delta.terminal_session_id}' does not match session ID '${descriptor.terminal_session_id}'`,
            )
          }
        }
      }
      if (result.end) {
        if (result.end.terminal_session_id !== descriptor.terminal_session_id) {
          throw new Error(
            `Terminal session end session ID '${result.end.terminal_session_id}' does not match session ID '${descriptor.terminal_session_id}'`,
          )
        }
      }
    } catch (validationError) {
      let cleanupSucceeded = false
      if (typeof this.adapter.cleanupSessions === "function") {
        try {
          const cleanupPromise = Promise.resolve().then(() =>
            this.adapter.cleanupSessions!({
              sessionIds: [descriptor.terminal_session_id],
              signal: "SIGKILL",
            }),
          )
          let timer: ReturnType<typeof setTimeout> | undefined
          const timeoutPromise = new Promise<{ settled: false; value: null }>((resolve) => {
            timer = setTimeout(() => resolve({ settled: false, value: null }), Math.max(1, this.terminationGraceMs))
          })
          let cleanupOutcome: { settled: boolean; value: unknown }
          try {
            cleanupOutcome = await Promise.race([
              cleanupPromise.then(
                (value) => ({ settled: true as const, value }),
                () => ({ settled: false as const, value: null }),
              ),
              timeoutPromise,
            ])
          } finally {
            if (timer !== undefined) clearTimeout(timer)
          }

          if (cleanupOutcome.settled && cleanupOutcome.value) {
            const rawClean = cleanupOutcome.value
            let cleanRes: TerminalCleanupResultV1 | undefined
            if (Array.isArray(rawClean) && rawClean.every((x) => typeof x === "string")) {
              cleanRes = {
                schema_version: "bb.terminal_cleanup_result.v1",
                cleanup_id: `cleanup-${Date.now()}`,
                scope: "filtered",
                cleaned_session_ids: [...rawClean],
                failed_session_ids: [],
              }
            } else if (rawClean && typeof rawClean === "object" && !Array.isArray(rawClean)) {
              const cleanObj = rawClean as {
                readonly cleanup_id?: string
                readonly scope?: "single" | "filtered" | "all"
                readonly cleaned_session_ids?: unknown
                readonly failed_session_ids?: unknown
              }
              if (
                (cleanObj.cleaned_session_ids === undefined ||
                  (Array.isArray(cleanObj.cleaned_session_ids) &&
                    cleanObj.cleaned_session_ids.every((x: unknown) => typeof x === "string"))) &&
                (cleanObj.failed_session_ids === undefined ||
                  (Array.isArray(cleanObj.failed_session_ids) &&
                    cleanObj.failed_session_ids.every((x: unknown) => typeof x === "string")))
              ) {
                cleanRes = assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
                  schema_version: "bb.terminal_cleanup_result.v1",
                  cleanup_id: cleanObj.cleanup_id ?? `cleanup-${Date.now()}`,
                  scope: cleanObj.scope ?? "filtered",
                  cleaned_session_ids: Array.isArray(cleanObj.cleaned_session_ids)
                    ? [...cleanObj.cleaned_session_ids]
                    : [],
                  failed_session_ids: Array.isArray(cleanObj.failed_session_ids)
                    ? [...cleanObj.failed_session_ids]
                    : [],
                })
              }
            }
            if (cleanRes) {
              const cleanedSet = new Set(cleanRes.cleaned_session_ids)
              const failedSet = new Set(cleanRes.failed_session_ids)
              const hasOverlap = [...cleanedSet].some((id) => failedSet.has(id))
              if (
                !hasOverlap &&
                cleanedSet.has(descriptor.terminal_session_id) &&
                !failedSet.has(descriptor.terminal_session_id)
              ) {
                cleanupSucceeded = true
              }
            }
          }
        } catch {}
      }
      if (!cleanupSucceeded) {
        this.rememberEndedSession(descriptor.terminal_session_id)
      }
      throw validationError
    }
    this.sessions.set(descriptor.terminal_session_id, {
      descriptor,
      end: result.end ?? null,
    })
    if (result.end) {
      this.sessions.delete(descriptor.terminal_session_id)
      this.rememberEndedSession(descriptor.terminal_session_id)
    } else {
      const endedIdx = this.endedSessionIds.indexOf(descriptor.terminal_session_id)
      if (endedIdx >= 0) {
        this.endedSessionIds.splice(endedIdx, 1)
      }
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
    if (result.outputDeltas) {
      for (const delta of result.outputDeltas) {
        if (delta.terminal_session_id !== record.descriptor.terminal_session_id) {
          throw new Error(
            `Terminal output delta session ID '${delta.terminal_session_id}' does not match interaction session ID '${record.descriptor.terminal_session_id}'`,
          )
        }
      }
    }
    if (result.end) {
      if (result.end.terminal_session_id !== record.descriptor.terminal_session_id) {
        throw new Error(
          `Terminal session end session ID '${result.end.terminal_session_id}' does not match interaction session ID '${record.descriptor.terminal_session_id}'`,
        )
      }
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
          : [...new Set([...this.sessions.keys(), ...this.endedSessionIds])]
    const targetSet = new Set(targetIds)
    const alreadyEnded = targetIds.filter((sessionId) => !this.sessions.has(sessionId) && this.endedSessionIds.includes(sessionId))
    const rawResult = this.adapter.cleanupSessions
      ? await this.adapter.cleanupSessions({ sessionIds: targetIds, signal: normalizeSignal(input.signal) })
      : {
          cleaned_session_ids: alreadyEnded,
          failed_session_ids: targetIds.filter((id) => !alreadyEnded.includes(id)),
        }
    let rawCleaned: readonly string[]
    let rawFailed: readonly string[]
    if (Array.isArray(rawResult)) {
      if (!rawResult.every((id) => typeof id === "string")) {
        throw new Error("Invalid cleanup result: array items must be strings")
      }
      rawCleaned = rawResult
      rawFailed = []
    } else if (rawResult && typeof rawResult === "object" && !Array.isArray(rawResult)) {
      const obj = rawResult as { readonly cleaned_session_ids?: unknown; readonly failed_session_ids?: unknown }
      if (obj.failed_session_ids !== undefined) {
        if (!Array.isArray(obj.failed_session_ids) || !obj.failed_session_ids.every((id) => typeof id === "string")) {
          throw new Error("Invalid cleanup result: failed_session_ids must be an array of strings")
        }
      }
      if (obj.cleaned_session_ids !== undefined) {
        if (!Array.isArray(obj.cleaned_session_ids) || !obj.cleaned_session_ids.every((id) => typeof id === "string")) {
          throw new Error("Invalid cleanup result: cleaned_session_ids must be an array of strings")
        }
      }
      rawFailed = (obj.failed_session_ids as readonly string[] | undefined) ?? []
      const failedSet = new Set(rawFailed)
      rawCleaned =
        (obj.cleaned_session_ids as readonly string[] | undefined) ??
        (obj.failed_session_ids ? targetIds.filter((id) => !failedSet.has(id)) : targetIds)
    } else {
      throw new Error("Invalid cleanup result: expected array of session IDs or cleanup result object")
    }

    // Validate disjoint before any state mutation
    const cleanedSetCheck = new Set(rawCleaned)
    for (const failedId of rawFailed) {
      if (cleanedSetCheck.has(failedId)) {
        throw new Error(`Invalid cleanup result: session ID '${failedId}' appears in both cleaned_session_ids and failed_session_ids`)
      }
    }

    const cleaned = rawCleaned.filter((id) => targetSet.has(id))
    const failed = rawFailed.filter((id) => targetSet.has(id))
    const cleanedSet = new Set([...cleaned, ...alreadyEnded])
    const finalFailed = Array.from(new Set([...failed, ...targetIds.filter((sessionId) => !cleanedSet.has(sessionId))]))

    for (const sessionId of cleanedSet) {
      if (targetSet.has(sessionId)) {
        this.sessions.delete(sessionId)
        this.rememberEndedSession(sessionId)
      }
    }
    return assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId || randomUUID(),
      scope: input.scope,
      cleaned_session_ids: [...cleanedSet],
      failed_session_ids: finalFailed,
      metadata: { signal: normalizeSignal(input.signal) },
    })
  }
}

export function makeOciTerminalSessionDriver(
  adapter: OciTerminalSessionAdapter,
  terminationGraceMs?: number,
): Pick<
  TerminalSessionDriverV1,
  "supportsTerminalSessions" | "startTerminalSession" | "interactTerminalSession" | "snapshotTerminalRegistry" | "cleanupTerminalSessions"
> {
  const manager = new OciTerminalSessionManager(adapter, terminationGraceMs)
  return {
    supportsTerminalSessions(
      capability: ExecutionCapabilityV1,
      placementClass: ExecutionPlacementV1["placement_class"],
    ): boolean {
      return (
        typeof adapter.cleanupSessions === "function" &&
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
