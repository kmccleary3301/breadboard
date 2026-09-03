import fs from "node:fs"
import { execFileSync, spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
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
  readonly identity: LocalTerminalSessionOwnershipIdentity
  readonly pgid: number | null
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

function isGroupAlive(pgid: number | null | undefined): boolean {
  if (pgid == null) return false
  if (process.platform === "win32") {
    try {
      process.kill(pgid, 0)
      return true
    } catch {
      return false
    }
  }
  try {
    process.kill(-pgid, 0)
    return true
  } catch (e: any) {
    if (e && e.code === "EPERM") return true
    return false
  }
}

function isProcessAlive(pid: number | null | undefined): boolean {
  if (pid == null) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (e: any) {
    if (e && e.code === "EPERM") return true
    return false
  }
}

export function getProcessStartToken(pid: number): string | null {
  if (pid <= 0) return null
  if (process.platform === "win32") return null
  if (process.platform === "linux") {
    try {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8")
      const closeParenIdx = stat.lastIndexOf(")")
      if (closeParenIdx >= 0) {
        const rest = stat.slice(closeParenIdx + 2).split(" ")
        if (rest.length >= 20) {
          return rest[19] ?? rest.slice(0, 20).join(" ")
        }
      }
      return stat
    } catch {
      return null
    }
  }
  try {
    const output = execFileSync("ps", ["-o", "lstart=", "-p", String(pid)], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 1000,
    })
    return output.trim() || null
  } catch {
    return null
  }
}
function killProcessTree(
  child: ChildProcessWithoutNullStreams | undefined,
  pgid: number | null | undefined,
  signal: NodeJS.Signals,
): void {
  const pid = pgid ?? child?.pid
  if (pid != null && process.platform === "win32") {
    try {
      const args = ["/pid", String(pid), "/T"]
      if (signal === "SIGKILL") {
        args.push("/F")
      }
      const taskkill = spawn("taskkill", args, { stdio: "ignore" })
      taskkill.on("error", () => {})
    } catch {}
    if (child) {
      try {
        child.kill(signal)
      } catch {}
    }
    return
  }
  if (pgid != null && process.platform !== "win32") {
    try {
      process.kill(-pgid, signal)
      return
    } catch {}
  }
  if (child) {
    try {
      child.kill(signal)
    } catch {}
  }
}
async function terminateChildProcessTree(
  child: ChildProcessWithoutNullStreams | undefined,
  initialSignal: NodeJS.Signals,
  graceMs = 250,
  storedPgid?: number | null,
  probeGroupAlive: (pgid: number | null | undefined) => boolean = isGroupAlive,
  probeKillTree: (child: ChildProcessWithoutNullStreams | undefined, pgid: number | null | undefined, signal: NodeJS.Signals) => void = killProcessTree,
): Promise<boolean> {
  const pgid = storedPgid ?? child?.pid
  if (pgid == null) return true

  if (!probeGroupAlive(pgid)) {
    return true
  }

  probeKillTree(child, pgid, initialSignal)

  const exitedGracefully = await new Promise<boolean>((resolve) => {
    let done = false
    const checkTimer = setInterval(() => {
      if (!probeGroupAlive(pgid)) {
        cleanup()
        resolve(true)
      }
    }, 20)

    const timeoutTimer = setTimeout(() => {
      cleanup()
      resolve(!probeGroupAlive(pgid))
    }, graceMs)

    function cleanup() {
      if (!done) {
        done = true
        clearInterval(checkTimer)
        clearTimeout(timeoutTimer)
      }
    }
  })

  if (exitedGracefully) {
    return true
  }

  probeKillTree(child, pgid, "SIGKILL")

  return new Promise<boolean>((resolve) => {
    let done = false
    const checkTimer = setInterval(() => {
      if (!probeGroupAlive(pgid)) {
        cleanup()
        resolve(true)
      }
    }, 20)

    const timeoutTimer = setTimeout(() => {
      cleanup()
      resolve(!probeGroupAlive(pgid))
    }, 500)

    function cleanup() {
      if (!done) {
        done = true
        clearInterval(checkTimer)
        clearTimeout(timeoutTimer)
      }
    }
  })
}

export interface LocalTerminalSessionOwnershipIdentity {
  readonly sessionId: string
  readonly leaderPid: number
  readonly startToken: string | null
  readonly startedAtMs: number
  readonly command: readonly string[]
}

export interface LocalTerminalSessionManagerOptions {
  readonly isGroupAlive?: (pgid: number | null | undefined) => boolean
  readonly isProcessAlive?: (pid: number | null | undefined) => boolean
  readonly getProcessStartToken?: (pid: number) => string | null
  readonly killProcessTree?: (
    child: ChildProcessWithoutNullStreams | undefined,
    pgid: number | null | undefined,
    signal: NodeJS.Signals,
  ) => void
  readonly validateGroupOwnership?: (
    identity: LocalTerminalSessionOwnershipIdentity,
    pgid: number,
  ) => boolean
}

export class LocalTerminalSessionManager {
  private readonly sessions = new Map<string, LocalTerminalSessionRecord>()
  private readonly endedSessionIds: string[] = []
  private readonly endedSessionPgids = new Map<string, number | null>()
  private readonly endedSessionIdentities = new Map<string, LocalTerminalSessionOwnershipIdentity>()
  private readonly probeGroupAlive: (pgid: number | null | undefined) => boolean
  private readonly probeProcessAlive: (pid: number | null | undefined) => boolean
  private readonly probeStartToken: (pid: number) => string | null
  private readonly probeKillTree: (
    child: ChildProcessWithoutNullStreams | undefined,
    pgid: number | null | undefined,
    signal: NodeJS.Signals,
  ) => void
  private readonly validateGroupOwnership: (
    identity: LocalTerminalSessionOwnershipIdentity,
    pgid: number,
  ) => boolean

  constructor(options?: LocalTerminalSessionManagerOptions) {
    this.probeGroupAlive = options?.isGroupAlive ?? isGroupAlive
    this.probeProcessAlive = options?.isProcessAlive ?? isProcessAlive
    this.probeStartToken = options?.getProcessStartToken ?? getProcessStartToken
    this.probeKillTree = options?.killProcessTree ?? killProcessTree
    this.validateGroupOwnership =
      options?.validateGroupOwnership ??
      ((identity, _pgid) => {
        if ((process.platform as string) === "win32") return false
        const isLeaderAlive = this.probeProcessAlive(identity.leaderPid)
        if (isLeaderAlive) {
          // If leader PID exists on the system, require captured start token match
          const currentToken = this.probeStartToken(identity.leaderPid)
          return (
            identity.startToken != null &&
            currentToken != null &&
            currentToken === identity.startToken
          )
        }
        // If leader PID is absent, original orphan descendant group still owns PGID
        const currentToken = this.probeStartToken(identity.leaderPid)
        return currentToken === null && !this.probeProcessAlive(identity.leaderPid)
      })
  }
  private rememberEndedSession(
    sessionId: string,
    pgid: number | null = null,
    identity?: LocalTerminalSessionOwnershipIdentity,
  ): void {
    const existingIndex = this.endedSessionIds.indexOf(sessionId)
    if (existingIndex >= 0) {
      this.endedSessionIds.splice(existingIndex, 1)
    }
    this.endedSessionIds.push(sessionId)
    if (pgid != null && this.probeGroupAlive(pgid)) {
      this.endedSessionPgids.set(sessionId, pgid)
      const effectiveIdentity = identity ?? {
        sessionId,
        leaderPid: pgid,
        startToken: null,
        startedAtMs: Date.now(),
        command: [],
      }
      this.endedSessionIdentities.set(sessionId, effectiveIdentity)
    } else {
      this.endedSessionPgids.delete(sessionId)
      this.endedSessionIdentities.delete(sessionId)
    }
  }
  supportsTerminalSessions(capability?: ExecutionCapabilityV1, placementClass?: ExecutionPlacementV1["placement_class"]): boolean {
    return (process.platform as string) !== "win32" && (placementClass === undefined || placementClass === "local_process") && (capability === undefined || capability.isolation_class === "process" || capability.isolation_class === "none")
  }

  async startSession(input: TerminalSessionStartInputV1): Promise<TerminalSessionStartResultV1> {
    if ((process.platform as string) === "win32") {
      throw new Error("Local terminal sessions with durable process tree cleanup are not supported on Windows (POSIX process groups unavailable)")
    }
    if (this.sessions.has(input.terminalSessionId) || this.endedSessionPgids.has(input.terminalSessionId)) {
      throw new Error(`Terminal session already active or pending cleanup: ${input.terminalSessionId}`)
    }
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
    const isPosix = (process.platform as string) !== "win32"
    const child = spawn(input.command[0]!, input.command.slice(1), {
      cwd: input.cwd ?? undefined,
      stdio: ["pipe", "pipe", "pipe"],
      detached: isPosix,
    })
    const leaderPid = child.pid ?? 0
    const startToken = leaderPid > 0 ? this.probeStartToken(leaderPid) : null
    const identity: LocalTerminalSessionOwnershipIdentity = {
      sessionId: descriptor.terminal_session_id,
      leaderPid,
      startToken,
      startedAtMs: Date.now(),
      command: descriptor.command,
    }
    const record: LocalTerminalSessionRecord = {
      descriptor,
      child,
      identity,
      pgid: leaderPid > 0 ? leaderPid : null,
      startedAtMs: identity.startedAtMs,
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
      this.rememberEndedSession(descriptor.terminal_session_id, record.pgid, record.identity)
    })
    this.sessions.set(descriptor.terminal_session_id, record)
    const endedIdx = this.endedSessionIds.indexOf(descriptor.terminal_session_id)
    if (endedIdx >= 0) {
      this.endedSessionIds.splice(endedIdx, 1)
    }
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
      killProcessTree(record.child, record.pgid ?? record.child.pid, normalizeSignal(input.signal))
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
      this.rememberEndedSession(record.descriptor.terminal_session_id, record.pgid, record.identity)
    }
    return { interaction, outputDeltas, end }
  }

  async snapshotRegistry(): Promise<TerminalRegistrySnapshotV1> {
    const activeRecords = [...this.sessions.values()].filter(
      (record) => record.pendingEnd === null,
    )
    const activeIds = new Set(
      activeRecords.map((record) => record.descriptor.terminal_session_id),
    )
    return assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", {
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: `term-reg:${Date.now()}`,
      active_sessions: activeRecords.map((record) => record.descriptor),
      ended_session_ids: this.endedSessionIds.filter((id) => !activeIds.has(id)),
    })
  }

  async cleanupSessions(input: TerminalSessionCleanupInputV1): Promise<TerminalCleanupResultV1> {
    const targetIds =
      input.scope === "single"
        ? input.sessionIds?.slice(0, 1) ?? []
        : input.scope === "filtered"
          ? input.sessionIds ?? []
          : [
              ...new Set([
                ...this.sessions.keys(),
                ...this.endedSessionIds,
                ...this.endedSessionPgids.keys(),
              ]),
            ]
    const cleaned: string[] = []
    const failed: string[] = []
    for (const sessionId of targetIds) {
      const record = this.sessions.get(sessionId)
      const pgid = record?.pgid ?? this.endedSessionPgids.get(sessionId) ?? null
      const child = record?.child
      const initialSignal = normalizeSignal(input.signal)

      if (record) {
        const isLeaderAlive = this.probeProcessAlive(record.identity.leaderPid)
        const isOwned = this.validateGroupOwnership(record.identity, pgid ?? record.identity.leaderPid)
        if (pgid != null && this.probeGroupAlive(pgid)) {
          if (!isOwned) {
            // Refuse to signal unvalidated group!
            // Transition active session to ended session tracking while retaining PGID and identity for retry
            this.sessions.delete(sessionId)
            this.rememberEndedSession(sessionId, pgid, record.identity)
            failed.push(sessionId)
            continue
          }

          const exited = await terminateChildProcessTree(
            isLeaderAlive ? child : undefined,
            initialSignal,
            250,
            pgid,
            this.probeGroupAlive,
            this.probeKillTree,
          )
          if (exited) {
            record.pendingEnd =
              record.pendingEnd ??
              buildTerminalEnd({
                descriptor: record.descriptor,
                state: "cleaned_up",
                durationMs: Date.now() - record.startedAtMs,
              })
            this.sessions.delete(sessionId)
            this.endedSessionPgids.delete(sessionId)
            this.endedSessionIdentities.delete(sessionId)
            this.rememberEndedSession(sessionId, null)
            cleaned.push(sessionId)
          } else {
            // Termination was not confirmed (timed out or resisted exit)
            // Transition active session to ended session tracking with PGID and identity for retry
            this.sessions.delete(sessionId)
            this.rememberEndedSession(sessionId, pgid, record.identity)
            failed.push(sessionId)
          }
        } else {
          record.pendingEnd =
            record.pendingEnd ??
            buildTerminalEnd({
              descriptor: record.descriptor,
              state: "cleaned_up",
              durationMs: Date.now() - record.startedAtMs,
            })
          this.sessions.delete(sessionId)
          this.endedSessionPgids.delete(sessionId)
          this.endedSessionIdentities.delete(sessionId)
          this.rememberEndedSession(sessionId, null)
          cleaned.push(sessionId)
        }
      } else if (this.endedSessionIds.includes(sessionId) || this.endedSessionPgids.has(sessionId)) {
        if (this.endedSessionPgids.has(sessionId)) {
          const orphanPgid = this.endedSessionPgids.get(sessionId)
          const identity = this.endedSessionIdentities.get(sessionId)
          const isAlive = orphanPgid != null ? this.probeGroupAlive(orphanPgid) : false

          if (!isAlive) {
            // Group is already dead on system; safely cleaned without signaling.
            this.endedSessionPgids.delete(sessionId)
            this.endedSessionIdentities.delete(sessionId)
            cleaned.push(sessionId)
          } else {
            // Group is alive. Validate ownership before signaling.
            const isOwned =
              orphanPgid != null && identity ? this.validateGroupOwnership(identity, orphanPgid) : false

            if (isOwned && orphanPgid != null) {
              const exited = await terminateChildProcessTree(
                undefined,
                initialSignal,
                250,
                orphanPgid,
                this.probeGroupAlive,
                this.probeKillTree,
              )
              if (exited) {
                this.endedSessionPgids.delete(sessionId)
                this.endedSessionIdentities.delete(sessionId)
                cleaned.push(sessionId)
              } else {
                // Termination unconfirmed: retain PGID and identity for retry
                failed.push(sessionId)
              }
            } else {
              // Original leader gone and identity cannot be proven.
              // Refuse to signal unverified group; report failed/unconfirmed to protect system.
              // Preserve endedSessionPgids & endedSessionIdentities so subsequent cleanup calls
              // never falsely downgrade to cleaned while the live descendant remains.
              failed.push(sessionId)
            }
          }
        } else {
          cleaned.push(sessionId)
        }
      } else {
        failed.push(sessionId)
      }
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
    return sharedLocalTerminalSessionManager.supportsTerminalSessions(capability, placementClass)
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
