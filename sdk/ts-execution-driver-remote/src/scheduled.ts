
import {
  assertValid,
  type ExecutionPlacementV1,
  type SandboxRequestV1,
  type SandboxResultV1,
} from "@breadboard/kernel-contracts"
import {
  buildAndPersistCanonicalSandboxEvidence,
  buildCanonicalSandboxSideEffectDigest,
  isPlacementCompatible,
  type ExecutionDriverExecutionContextV1,
  type ExecutionDriverTerminationContextV1,
  type ExecutionDriverV1,
} from "@breadboard/execution-drivers"
import { buildRemoteSandboxRequest } from "./index.js"

export function canonicalScheduledRequestKey(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value)
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("scheduled request contains a non-finite number")
    }
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalScheduledRequestKey(item)).join(",")}]`
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalScheduledRequestKey(record[key])}`)
      .join(",")}}`
  }
  throw new Error("scheduled request contains a non-JSON value")
}

export type ScheduledExecutionStateV1 =
  | "accepted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out"

export interface ScheduledExecutionHandleV1 {
  readonly executionId: string
  readonly evidenceRefs?: readonly string[]
}

export interface ScheduledExecutionObservationV1 {
  readonly state: ScheduledExecutionStateV1
  readonly result?: SandboxResultV1
  readonly evidenceRefs?: readonly string[]
}

export interface ScheduledExecutionBackendV1 {
  readonly backendId: string
  submit(
    request: SandboxRequestV1,
    context: ExecutionDriverExecutionContextV1,
  ): Promise<ScheduledExecutionHandleV1>
  observe(executionId: string): Promise<ScheduledExecutionObservationV1>
  cancel(
    executionId: string,
    context: ExecutionDriverTerminationContextV1,
  ): Promise<void>
}

export interface ScheduledExecutionEvidenceV1 {
  readonly driverId: string
  readonly backendId: string
  readonly executionId: string
  readonly state: ScheduledExecutionStateV1
  readonly evidenceRefs: readonly string[]
}

export interface ScheduledExecutionDriverOptionsV1 {
  readonly driverId: string
  readonly placementClass: ExecutionPlacementV1["placement_class"]
  readonly pollIntervalMs?: number
  readonly cancellationObservationTimeoutMs?: number
  readonly recordEvidence: (
    evidence: ScheduledExecutionEvidenceV1,
  ) => Promise<void> | void
}
export interface ScheduledExecutionDriverRegistrationV1 {
  readonly backend: ScheduledExecutionBackendV1
  readonly options: Omit<
    ScheduledExecutionDriverOptionsV1,
    "driverId" | "placementClass"
  >
}
interface ActiveScheduledExecution {
  readonly handle: Promise<ScheduledExecutionHandleV1>
  readonly requestKey: string
  result?: Promise<SandboxResultV1>
  executionId?: string
  cancellation?: Promise<SandboxResultV1>
  lastEvidenceKey?: string
  terminalObserved: boolean
  terminalEvidenceKey?: string
  terminalObservation?: ScheduledExecutionObservationV1
  evidenceTail: Promise<void>
  failedEvidence?: {
    readonly key: string
    readonly evidence: ScheduledExecutionEvidenceV1
  }
}

const TERMINAL_STATES = new Set<ScheduledExecutionStateV1>([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
])
const NONTERMINAL_STATES = new Set<ScheduledExecutionStateV1>([
  "accepted",
  "running",
])

function requireNonempty(value: string, name: string): string {
  if (!value.trim()) throw new Error(`${name} must be non-empty`)
  return value
}

function evidenceRefs(...groups: (readonly string[] | undefined)[]): string[] {
  const refs = groups.flatMap((group) => group ?? [])
  if (refs.some((ref) => typeof ref !== "string" || !ref.trim())) {
    throw new Error("scheduled execution evidence refs must be non-empty strings")
  }
  return [...new Set(refs)].sort()
}

function waitForPoll(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(signal.reason)
  return new Promise((resolve, reject) => {
    const timer = setTimeout(done, milliseconds)
    function done(): void {
      signal.removeEventListener("abort", aborted)
      resolve()
    }
    function aborted(): void {
      clearTimeout(timer)
      reject(signal.reason)
    }
    signal.addEventListener("abort", aborted, { once: true })
  })
}


async function settleBefore<T>(
  promise: Promise<T>,
  deadline: number,
  message: string,
): Promise<T> {
  const remaining = deadline - Date.now()
  if (remaining <= 0) {
    void promise.catch(() => undefined)
    throw new Error(message)
  }
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error(message)), remaining)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

async function scheduledFailureResult(
  request: SandboxRequestV1,
  status: Exclude<SandboxResultV1["status"], "completed">,
  reason: string,
): Promise<SandboxResultV1> {
  const evidence = await buildAndPersistCanonicalSandboxEvidence({
    command: request.command,
    status,
    exitCode: null,
    stdout: "",
    stderr: "",
    evidenceMode: request.evidence_mode,
  })
  return assertValid<SandboxResultV1>("sandboxResult", {
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status,
    ...evidence,
    error: { reason },
  })
}
function requireCanonicalExitCode(value: unknown): number | null {
  if (value === null) return null
  if (typeof value === "number" && Number.isSafeInteger(value)) return value
  throw new Error("scheduled execution result requires canonical exit-code usage")
}


function assertProviderNeutralResultEvidence(
  request: SandboxRequestV1,
  result: SandboxResultV1,
): void {
  const digest = /^sha256:[0-9a-f]{64}$/
  if (!result.stdout_ref || !digest.test(result.stdout_ref)) {
    throw new Error("scheduled execution result requires provider-neutral stdout evidence")
  }
  if (!result.stderr_ref || !digest.test(result.stderr_ref)) {
    throw new Error("scheduled execution result requires provider-neutral stderr evidence")
  }
  if (
    result.artifact_refs?.length !== 2
    || result.artifact_refs[0] !== result.stdout_ref
    || result.artifact_refs[1] !== result.stderr_ref
  ) {
    throw new Error("scheduled execution result requires canonical output artifact evidence")
  }
  if (
    result.usage == null
    || Object.keys(result.usage).some((key) => key !== "exit_code")
    || !("exit_code" in result.usage)
  ) {
    throw new Error("scheduled execution result requires canonical exit-code usage")
  }
  const exitCode = requireCanonicalExitCode(result.usage.exit_code)
  if (result.status === "completed" && exitCode !== 0) {
    throw new Error("completed scheduled execution result requires exit code zero")
  }
  const expectedSideEffectDigest = buildCanonicalSandboxSideEffectDigest({
    command: request.command,
    status: result.status,
    exitCode,
    stdoutRef: result.stdout_ref,
    stderrRef: result.stderr_ref,
  })
  if (result.side_effect_digest !== expectedSideEffectDigest) {
    throw new Error("scheduled execution result side-effect digest does not match its evidence")
  }
  const expectedEvidenceRefs = request.evidence_mode === "minimal"
    ? []
    : [expectedSideEffectDigest]
  if (
    result.evidence_refs?.length !== expectedEvidenceRefs.length
    || expectedEvidenceRefs.some((ref) => !result.evidence_refs?.includes(ref))
  ) {
    throw new Error("scheduled execution result has invalid canonical evidence references")
  }
  if (result.placement_id != null) {
    throw new Error("scheduled execution result contains provider-specific result metadata")
  }
}

async function terminalResult(
  request: SandboxRequestV1,
  observation: ScheduledExecutionObservationV1,
): Promise<SandboxResultV1> {
  const expectedStatus = observation.state === "timed_out"
    ? "timed_out"
    : observation.state === "cancelled"
      ? "cancelled"
      : observation.state === "completed"
        ? "completed"
        : "failed"
  if (observation.result) {
    const result = assertValid<SandboxResultV1>("sandboxResult", observation.result)
    if (result.request_id !== request.request_id) {
      throw new Error("scheduled execution result request_id does not match request")
    }
    if (result.status !== expectedStatus) {
      throw new Error("scheduled execution result status does not match terminal observation")
    }
    assertProviderNeutralResultEvidence(request, result)
    return result
  }
  if (observation.state === "completed") {
    throw new Error("completed scheduled execution has no sandbox result")
  }
  const status = expectedStatus as Exclude<SandboxResultV1["status"], "completed">
  return scheduledFailureResult(
    request,
    status,
    `execution_${status}`,
  )
}

function backendFailure(driverId: string, operation: string, cause: unknown): Error {
  return new Error(`${driverId} backend ${operation} failed`, { cause })
}

export function makeScheduledExecutionDriver(
  backend: ScheduledExecutionBackendV1,
  options: ScheduledExecutionDriverOptionsV1,
): ExecutionDriverV1 {
  requireNonempty(backend.backendId, "backendId")
  const driverId = requireNonempty(options.driverId, "driverId")
  if (typeof options.recordEvidence !== "function") {
    throw new Error(`${driverId} execution driver requires an evidence sink`)
  }
  const pollIntervalMs = options.pollIntervalMs ?? 250
  if (!Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 1) {
    throw new Error("scheduled execution pollIntervalMs must be a positive safe integer")
  }
  const cancellationObservationTimeoutMs =
    options.cancellationObservationTimeoutMs ?? 2_000
  if (
    !Number.isSafeInteger(cancellationObservationTimeoutMs)
    || cancellationObservationTimeoutMs < pollIntervalMs
  ) {
    throw new Error(
      "scheduled execution cancellationObservationTimeoutMs must be a safe integer at least pollIntervalMs",
    )
  }
  const active = new Map<string, ActiveScheduledExecution>()

  async function recordEvidence(
    entry: ActiveScheduledExecution,
    handle: ScheduledExecutionHandleV1,
    observation: ScheduledExecutionObservationV1,
    deadline?: number,
  ): Promise<boolean> {
    const refs = evidenceRefs(
      handle.evidenceRefs,
      observation.evidenceRefs,
    )
    const key = JSON.stringify([observation.state, refs])
    const mayRecord =
      entry.terminalEvidenceKey === undefined
      || entry.terminalEvidenceKey === key
    if (TERMINAL_STATES.has(observation.state) && mayRecord) {
      entry.terminalEvidenceKey = key
      entry.terminalObservation ??= observation
    }
    if (entry.lastEvidenceKey === key) return true
    const evidence: ScheduledExecutionEvidenceV1 = {
      driverId,
      backendId: backend.backendId,
      executionId: handle.executionId,
      state: observation.state,
      evidenceRefs: refs,
    }
    const writeEvidence = async (
      value: ScheduledExecutionEvidenceV1,
    ): Promise<void> => {
      const write = Promise.resolve().then(() => options.recordEvidence(value))
      if (deadline === undefined) {
        await write
        return
      }
      await settleBefore(
        write,
        deadline,
        `${driverId} evidence recording exceeded the cleanup deadline`,
      )
    }
    entry.evidenceTail = entry.evidenceTail
      .catch(() => undefined)
      .then(async () => {
        const failed = entry.failedEvidence
        if (failed) {
          if (
            failed.evidence.state === "accepted"
            || entry.terminalEvidenceKey === undefined
            || entry.terminalEvidenceKey === failed.key
          ) {
            await writeEvidence(failed.evidence)
            entry.lastEvidenceKey = failed.key
          }
          entry.failedEvidence = undefined
        }
        if (
          observation.state !== "accepted"
          && entry.terminalEvidenceKey !== undefined
          && entry.terminalEvidenceKey !== key
        ) return
        if (entry.lastEvidenceKey === key) return
        try {
          await writeEvidence(evidence)
          entry.lastEvidenceKey = key
        } catch (error: unknown) {
          entry.failedEvidence = { key, evidence }
          throw error
        }
      })
    if (deadline === undefined) {
      await entry.evidenceTail
    } else {
      await settleBefore(
        entry.evidenceTail,
        deadline,
        `${driverId} evidence queue exceeded the cleanup deadline`,
      )
    }
    return mayRecord
  }

  async function recordUnconfirmedCancellation(
    entry: ActiveScheduledExecution,
    handle: ScheduledExecutionHandleV1,
    deadline: number,
  ): Promise<void> {
    await recordEvidence(entry, handle, {
      state: "running",
      evidenceRefs: [
        `scheduled://${encodeURIComponent(backend.backendId)}/${encodeURIComponent(handle.executionId)}/cancellation-unconfirmed`,
      ],
    }, deadline)
  }

  async function recordTerminalCancellationEvidence(
    entry: ActiveScheduledExecution,
    handle: ScheduledExecutionHandleV1,
    observation: ScheduledExecutionObservationV1,
    deadline: number,
  ): Promise<void> {
    while (true) {
      try {
        await recordEvidence(entry, handle, observation, deadline)
        return
      } catch (error: unknown) {
        const remainingMs = deadline - Date.now()
        if (remainingMs <= 0) {
          throw backendFailure(driverId, "terminal cancellation evidence", error)
        }
        await new Promise((resolve) => {
          setTimeout(resolve, Math.min(pollIntervalMs, remainingMs))
        })
      }
    }
  }

  function requestCancellation(
    entry: ActiveScheduledExecution,
    request: SandboxRequestV1,
    context: ExecutionDriverTerminationContextV1,
  ): Promise<SandboxResultV1> {
    if (entry.cancellation) return entry.cancellation
    const deadline = Date.now() + cancellationObservationTimeoutMs
    const cancellation = (async (): Promise<SandboxResultV1> => {
      let handle: ScheduledExecutionHandleV1
      try {
        handle = await settleBefore(
          entry.handle,
          deadline,
          `${driverId} submission did not settle before the cleanup deadline`,
        )
      } catch (error: unknown) {
        void entry.handle
          .then(async (lateHandle) => {
            const lateDeadline = Date.now() + cancellationObservationTimeoutMs
            const lateController = new AbortController()
            try {
              await settleBefore(
                backend.cancel(lateHandle.executionId, {
                  reason: context.reason,
                  signal: lateController.signal,
                  deadlineAtMs: lateDeadline,
                }),
                lateDeadline,
                `${driverId} late submission cancellation exceeded the cleanup deadline`,
              )
            } finally {
              lateController.abort()
            }
          })
          .catch(() => undefined)
        throw backendFailure(driverId, "cancellation submission", error)
      }
      const cleanupController = new AbortController()
      const cleanupContext: ExecutionDriverTerminationContextV1 = {
        reason: context.reason,
        signal: cleanupController.signal,
        deadlineAtMs: deadline,
      }
      try {
        await settleBefore(
          backend.cancel(handle.executionId, cleanupContext),
          deadline,
          `${driverId} cancellation exceeded the cleanup deadline`,
        )
      } catch (error: unknown) {
        cleanupController.abort(error)
        try {
          await recordUnconfirmedCancellation(entry, handle, deadline)
        } catch {
          // Preserve the primary cancellation failure after the bounded evidence attempt.
        }
        throw backendFailure(driverId, "cancellation", error)
      }
      while (true) {
        let observation: ScheduledExecutionObservationV1
        try {
          observation = await settleBefore(
            backend.observe(handle.executionId),
            deadline,
            `${driverId} cancellation observation exceeded the cleanup deadline`,
          )
        } catch (error: unknown) {
          try {
            await recordUnconfirmedCancellation(entry, handle, deadline)
          } catch {
            // Preserve the primary observation failure after the bounded evidence attempt.
          }
          throw backendFailure(driverId, "cancellation observation", error)
        }
        const terminal = TERMINAL_STATES.has(observation.state)
        if (!terminal && !NONTERMINAL_STATES.has(observation.state)) {
          throw new Error(`${driverId} backend returned unknown execution state`)
        }
        if (terminal) {
          await recordTerminalCancellationEvidence(
            entry,
            handle,
            observation,
            deadline,
          )
        } else {
          await recordEvidence(entry, handle, observation, deadline)
        }
        if (terminal) {
          entry.terminalObserved = true
          return terminalResult(
            request,
            entry.terminalObservation ?? observation,
          )
        }
        const remainingMs = deadline - Date.now()
        if (remainingMs <= 0) {
          throw backendFailure(
            driverId,
            "cancellation observation",
            new Error(`${driverId} cancellation observation exceeded the cleanup deadline`),
          )
        }
        await new Promise((resolve) => {
          setTimeout(resolve, Math.min(pollIntervalMs, remainingMs))
        })
      }
    })()
    entry.cancellation = cancellation
    void cancellation.catch(() => {
      if (entry.cancellation === cancellation) entry.cancellation = undefined
    })
    return cancellation
  }

  return {
    driverId,
    supportedPlacements: [options.placementClass],
    supportsCapability(capability, placementClass) {
      return placementClass === options.placementClass
        && isPlacementCompatible(capability, placementClass)
    },
    buildSandboxRequest({
      requestId,
      capability,
      command,
      workspaceRef,
      imageRef,
      placement,
      metadata,
    }) {
      if (command.length === 0) {
        throw new Error(`${driverId} execution driver requires a non-empty command`)
      }
      const request = buildRemoteSandboxRequest({
        requestId,
        capability,
        command,
        workspaceRef,
        imageRef,
        placementClass: placement.placement_class,
        metadata,
      })
      return {
        ...request,
        metadata: {
          ...(request.metadata ?? {}),
          driver: driverId,
        },
      }
    },
    async execute(request, context) {
      if (!context) throw new Error(`${driverId} execution requires a lifecycle context`)
      const requestKey = canonicalScheduledRequestKey(request)
      const existing = active.get(request.request_id)
      if (existing) {
        if (existing.requestKey !== requestKey) {
          return scheduledFailureResult(
            request,
            "failed",
            "request_id_collision",
          )
        }
        if (!existing.result) {
          throw new Error(`${driverId} active execution has no result promise`)
        }
        return existing.result
      }
      let entry: ActiveScheduledExecution
      const handlePromise = Promise.resolve()
        .then(() => backend.submit(request, context))
        .catch((error: unknown) => {
          throw backendFailure(driverId, "submission", error)
        })
        .then((handle) => {
          const normalized = {
            executionId: requireNonempty(handle.executionId, "executionId"),
            evidenceRefs: evidenceRefs(handle.evidenceRefs),
          }
          entry.executionId = normalized.executionId
          return normalized
        })
      entry = {
        handle: handlePromise,
        requestKey,
        evidenceTail: Promise.resolve(),
        terminalObserved: false,
      }
      const result = (async (): Promise<SandboxResultV1> => {
        try {
          const handle = await handlePromise
          await recordEvidence(entry, handle, {
            state: "accepted",
            evidenceRefs: handle.evidenceRefs,
          })
          if (entry.cancellation) {
            await entry.cancellation
            throw context.signal.reason ?? new Error(`${driverId} execution was cancelled`)
          }
          while (true) {
            let observation: ScheduledExecutionObservationV1
            try {
              observation = await backend.observe(handle.executionId)
            } catch (error: unknown) {
              throw backendFailure(driverId, "observation", error)
            }
            const terminal = TERMINAL_STATES.has(observation.state)
            if (!terminal && !NONTERMINAL_STATES.has(observation.state)) {
              throw new Error(`${driverId} backend returned unknown execution state`)
            }
            await recordEvidence(entry, handle, observation)
            if (terminal) {
              entry.terminalObserved = true
              return terminalResult(
                request,
                entry.terminalObservation ?? observation,
              )
            }
            await waitForPoll(pollIntervalMs, context.signal)
          }
        } finally {
          if (
            (entry.terminalObserved || entry.executionId === undefined)
            && active.get(request.request_id) === entry
          ) {
            active.delete(request.request_id)
          }
        }
      })()
      entry.result = result
      active.set(request.request_id, entry)
      return result
    },
    async terminate(request, context) {
      const entry = active.get(request.request_id)
      if (!entry) return
      let result: SandboxResultV1
      try {
        result = await requestCancellation(entry, request, context)
      } catch (error: unknown) {
        if (
          entry.executionId === undefined
          || entry.terminalObservation !== undefined
        ) {
          active.delete(request.request_id)
        }
        throw error
      }
      if (active.get(request.request_id) === entry) {
        active.delete(request.request_id)
      }
      return result
    },
  }
}

export function makeRayExecutionDriver(
  backend: ScheduledExecutionBackendV1,
  options: Omit<ScheduledExecutionDriverOptionsV1, "driverId" | "placementClass">,
): ExecutionDriverV1 {
  return makeScheduledExecutionDriver(backend, {
    ...options,
    driverId: "ray",
    placementClass: "delegated_python",
  })
}

export function makeSlurmExecutionDriver(
  backend: ScheduledExecutionBackendV1,
  options: Omit<ScheduledExecutionDriverOptionsV1, "driverId" | "placementClass">,
): ExecutionDriverV1 {
  return makeScheduledExecutionDriver(backend, {
    ...options,
    driverId: "slurm",
    placementClass: "remote_worker",
  })
}
