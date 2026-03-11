import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import { randomUUID } from "node:crypto"
import { setTimeout as sleep } from "node:timers/promises"

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

interface LocalTerminalSessionRecord {
  readonly descriptor: TerminalSessionDescriptorV1
  readonly child: ChildProcessWithoutNullStreams
  readonly startedAtMs: number
  nextChunkSeq: number
  pendingOutput: TerminalOutputDeltaV1[]
  pendingEnd: TerminalSessionEndV1 | null
  deliveredEnd: boolean
}

function encodeChunk(text: string): string {
  return Buffer.from(text, "utf8").toString("base64")
}

function decodeChunk(input: { inputText?: string | null; inputB64?: string | null }): string {
  if (input.inputText != null) {
    return input.inputText
  }
  if (input.inputB64 != null) {
    return Buffer.from(input.inputB64, "base64").toString("utf8")
  }
  return ""
}

function buildTerminalEnd(input: {
  descriptor: TerminalSessionDescriptorV1
  state: TerminalSessionEndV1["terminal_state"]
  causingCallId?: string | null
  exitCode?: number | null
  durationMs: number
}): TerminalSessionEndV1 {
  return assertValid<TerminalSessionEndV1>("terminalSessionEnd", {
    schema_version: "bb.terminal_session_end.v1",
    terminal_session_id: input.descriptor.terminal_session_id,
    startup_call_id: input.descriptor.startup_call_id ?? null,
    causing_call_id: input.causingCallId ?? null,
    terminal_state: input.state,
    exit_code: input.exitCode ?? null,
    duration_ms: input.durationMs,
    artifact_refs: [],
    evidence_refs: [],
  })
}

function normalizeSignal(signal?: string | null): NodeJS.Signals {
  return (signal ?? "SIGTERM") as NodeJS.Signals
}

export class LocalTerminalSessionManager {
  private readonly sessions = new Map<string, LocalTerminalSessionRecord>()
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

  async startSession(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1> {
    if (input.command.length === 0) {
      throw new Error("Terminal sessions require a non-empty command")
    }
    const descriptor = assertValid<TerminalSessionDescriptorV1>("terminalSessionDescriptor", {
      schema_version: "bb.terminal_session_descriptor.v1",
      terminal_session_id: input.terminalSessionId,
      startup_call_id: input.startupCallId ?? null,
      owner_task_id: input.ownerTaskId ?? null,
      public_handles: input.publicHandles ?? [{ namespace: "breadboard", label: "session_id", value: input.terminalSessionId, audience: "host" }],
      command: input.command,
      cwd: input.cwd ?? null,
      stream_mode: input.streamMode ?? "pipes",
      stream_split: input.streamSplit ?? "stdout_stderr",
      capability_id: input.capability?.capability_id ?? null,
      placement_id: input.placement?.placement_id ?? null,
      persistence_scope: input.persistenceScope ?? "thread",
      continuation_scope: input.continuationScope ?? "both",
    })
    const child = spawn(input.command[0]!, input.command.slice(1), {
      cwd: input.cwd ?? undefined,
      stdio: ["pipe", "pipe", "pipe"],
    })
    const record: LocalTerminalSessionRecord = {
      descriptor,
      child,
      startedAtMs: Date.now(),
      nextChunkSeq: 0,
      pendingOutput: [],
      pendingEnd: null,
      deliveredEnd: false,
    }

    const enqueue = (stream: TerminalOutputDeltaV1["stream"], chunk: string | Buffer): void => {
      const text = String(chunk)
      if (text.length === 0) return
      record.pendingOutput.push(
        assertValid<TerminalOutputDeltaV1>("terminalOutputDelta", {
          schema_version: "bb.terminal_output_delta.v1",
          terminal_session_id: descriptor.terminal_session_id,
          startup_call_id: descriptor.startup_call_id ?? null,
          causing_call_id: null,
          stream,
          chunk_b64: encodeChunk(text),
          chunk_seq: record.nextChunkSeq++,
        }),
      )
    }

    child.stdout.on("data", (chunk) => enqueue("stdout", chunk))
    child.stderr.on("data", (chunk) => enqueue("stderr", chunk))
    child.on("close", (exitCode, signal) => {
      if (record.pendingEnd) {
        return
      }
      const state: TerminalSessionEndV1["terminal_state"] = signal ? "cancelled" : exitCode === 0 ? "completed" : "failed"
      record.pendingEnd = buildTerminalEnd({
        descriptor,
        state,
        exitCode: exitCode ?? null,
        durationMs: Date.now() - record.startedAtMs,
      })
      this.rememberEndedSession(descriptor.terminal_session_id)
    })

    this.sessions.set(descriptor.terminal_session_id, record)
    return {
      descriptor,
      outputDeltas: [],
    }
  }

  async interactSession(input: TerminalSessionInteractionInputV1): Promise<TerminalSessionInteractionResultV1> {
    const record = this.sessions.get(input.terminalSessionId)
    if (!record) {
      throw new Error(`Unknown terminal session: ${input.terminalSessionId}`)
    }
    const interaction = assertValid<TerminalInteractionV1>("terminalInteraction", {
      schema_version: "bb.terminal_interaction.v1",
      terminal_session_id: record.descriptor.terminal_session_id,
      startup_call_id: record.descriptor.startup_call_id ?? null,
      causing_call_id: input.causingCallId ?? null,
      interaction_kind: input.interactionKind,
      input_b64:
        input.interactionKind === "stdin"
          ? input.inputB64 ?? encodeChunk(decodeChunk(input))
          : null,
      signal: input.interactionKind === "signal" ? input.signal ?? "SIGTERM" : null,
    })

    if (input.interactionKind === "stdin") {
      record.child.stdin.write(decodeChunk(input))
    } else if (input.interactionKind === "signal") {
      record.child.kill(normalizeSignal(input.signal))
    }

    if ((input.settleMs ?? 0) > 0) {
      await sleep(input.settleMs ?? 0)
    }

    const outputDeltas = [...record.pendingOutput]
    record.pendingOutput = []
    let end: TerminalSessionEndV1 | undefined
    if (record.pendingEnd && !record.deliveredEnd) {
      end = record.pendingEnd
      record.deliveredEnd = true
      this.sessions.delete(record.descriptor.terminal_session_id)
      this.rememberEndedSession(record.descriptor.terminal_session_id)
    }
    return { interaction, outputDeltas, end }
  }

  async snapshotRegistry(): Promise<TerminalRegistrySnapshotV1> {
    return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", {
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: `term-reg:${Date.now()}`,
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
    const cleaned: string[] = []
    const failed: string[] = []
    for (const sessionId of targetIds) {
      const record = this.sessions.get(sessionId)
      if (!record) {
        failed.push(sessionId)
        continue
      }
      record.child.kill(normalizeSignal(input.signal))
      record.pendingEnd =
        record.pendingEnd ??
        buildTerminalEnd({
          descriptor: record.descriptor,
          state: "cleaned_up",
          durationMs: Date.now() - record.startedAtMs,
        })
      cleaned.push(sessionId)
      this.sessions.delete(sessionId)
      this.rememberEndedSession(sessionId)
    }
    return assertValid<TerminalCleanupResultV1>("terminalCleanupResult", {
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId || randomUUID(),
      scope: input.scope,
      cleaned_session_ids: cleaned,
      failed_session_ids: failed,
      metadata: { signal: normalizeSignal(input.signal) },
    })
  }
}

const sharedLocalTerminalSessionManager = new LocalTerminalSessionManager()

export const trustedLocalTerminalSessionDriver: Pick<
  TerminalSessionDriverV1,
  "supportsTerminalSessions" | "startTerminalSession" | "interactTerminalSession" | "snapshotTerminalRegistry" | "cleanupTerminalSessions"
> = {
  supportsTerminalSessions(capability: ExecutionCapabilityV1, placementClass: ExecutionPlacementV1["placement_class"]): boolean {
    return placementClass === "local_process" && (capability.isolation_class === "process" || capability.isolation_class === "none")
  },
  startTerminalSession(input) {
    return sharedLocalTerminalSessionManager.startSession(input)
  },
  interactTerminalSession(input) {
    return sharedLocalTerminalSessionManager.interactSession(input)
  },
  snapshotTerminalRegistry() {
    return sharedLocalTerminalSessionManager.snapshotRegistry()
  },
  cleanupTerminalSessions(input) {
    return sharedLocalTerminalSessionManager.cleanupSessions(input)
  },
}
