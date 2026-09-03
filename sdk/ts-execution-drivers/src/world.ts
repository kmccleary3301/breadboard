import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type SandboxRequestV1,
  type SandboxResultV1,
  type TerminalCleanupResultV1,
  type TerminalInteractionV1,
  type TerminalOutputDeltaV1,
  type TerminalRegistrySnapshotV1,
  type TerminalSessionDescriptorV1,
  type TerminalSessionEndV1,
  type UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"
import {
  buildExecutionDriverUnsupportedCase,
  buildPlannedExecution,
  selectExecutionDriver,
  selectTerminalSessionDriver,
  type ExecutionDriverV1,
  type PlannedExecutionV1,
  type TerminalSessionCleanupInputV1,
  type TerminalSessionDriverV1,
  type TerminalSessionInteractionInputV1,
  type TerminalSessionInteractionResultV1,
  type TerminalSessionStartInputV1,
  type TerminalSessionStartResultV1,
} from "./index.js"

export type ExecutionDriverHintV1 = "trusted_local" | "oci" | "remote"

export interface ExecutionDriverExecutionContextV1 {
  readonly signal: AbortSignal
  readonly deadlineAtMs: number | null
  readonly terminationGraceMs: number
  readonly capability?: ExecutionCapabilityV1
  readonly placement?: ExecutionPlacementV1
  readonly driverId?: string
}

export interface ExecutionDriverTerminationContextV1 {
  readonly reason: "deadline" | "cancelled"
  readonly signal: AbortSignal
  readonly deadlineAtMs: number | null
}

export interface ExecutionLivenessEvidenceV1 {
  readonly observed: boolean
  readonly executionStarted: boolean
  readonly state: "unsupported" | "running" | "completed" | "failed" | "cancelled" | "timed_out"
  readonly terminationRequested: boolean
  readonly terminationObserved: boolean
  readonly observedAtUtc: string
  readonly evidenceRefs: string[]
}

export interface ExecutionWorldSandboxInputV1 {
  readonly kind: "sandbox"
  readonly capability: ExecutionCapabilityV1
  readonly placement: ExecutionPlacementV1
  readonly requestId: string
  readonly command?: string[] | null
  readonly workspaceRef?: string | null
  readonly imageRef?: string | null
  readonly metadata?: Record<string, unknown>
  readonly driverIdHint?: ExecutionDriverHintV1
  readonly deadlineMs?: number | null
  readonly terminationGraceMs?: number
  readonly signal?: AbortSignal
  readonly onTimeout?: (input: {
    driverId: string
    request: SandboxRequestV1
    liveness: ExecutionLivenessEvidenceV1
  }) => void | Promise<void>
}

export interface ExecutionWorldSandboxResultV1 {
  readonly kind: "sandbox"
  readonly driverId: string | null
  readonly plan: PlannedExecutionV1 | null
  readonly sandboxResult: SandboxResultV1 | null
  readonly livenessEvidence: ExecutionLivenessEvidenceV1
  readonly unsupportedCase?: UnsupportedCaseV1
}

export interface ExecutionWorldTerminalStartOperationV1 {
  readonly kind: "terminal_start"
  readonly capability: ExecutionCapabilityV1
  readonly placement: ExecutionPlacementV1
  readonly input: TerminalSessionStartInputV1
  readonly driverIdHint?: ExecutionDriverHintV1
}

export interface ExecutionWorldTerminalInteractionOperationV1 {
  readonly kind: "terminal_interact"
  readonly capability: ExecutionCapabilityV1
  readonly placement: ExecutionPlacementV1
  readonly input: TerminalSessionInteractionInputV1
  readonly driverIdHint?: ExecutionDriverHintV1
}

export interface ExecutionWorldTerminalSnapshotOperationV1 {
  readonly kind: "terminal_snapshot"
  readonly capability: ExecutionCapabilityV1
  readonly placement: ExecutionPlacementV1
  readonly driverIdHint?: ExecutionDriverHintV1
}

export interface ExecutionWorldTerminalCleanupOperationV1 {
  readonly kind: "terminal_cleanup"
  readonly capability: ExecutionCapabilityV1
  readonly placement: ExecutionPlacementV1
  readonly input: TerminalSessionCleanupInputV1
  readonly driverIdHint?: ExecutionDriverHintV1
}

export type ExecutionWorldOperationV1 =
  | ExecutionWorldSandboxInputV1
  | ExecutionWorldTerminalStartOperationV1
  | ExecutionWorldTerminalInteractionOperationV1
  | ExecutionWorldTerminalSnapshotOperationV1
  | ExecutionWorldTerminalCleanupOperationV1

export interface ExecutionWorldTerminalStartResultV1 {
  readonly kind: "terminal_start"
  readonly driverId: string | null
  readonly capability: ExecutionCapabilityV1
  readonly placement: ExecutionPlacementV1
  readonly result: TerminalSessionStartResultV1 | null
  readonly unsupportedCase?: UnsupportedCaseV1
}

export interface ExecutionWorldTerminalInteractionResultV1 {
  readonly kind: "terminal_interact"
  readonly driverId: string | null
  readonly result: TerminalSessionInteractionResultV1 | null
  readonly unsupportedCase?: UnsupportedCaseV1
}

export interface ExecutionWorldTerminalSnapshotResultV1 {
  readonly kind: "terminal_snapshot"
  readonly driverId: string | null
  readonly result: TerminalRegistrySnapshotV1 | null
  readonly unsupportedCase?: UnsupportedCaseV1
}

export interface ExecutionWorldTerminalCleanupResultV1 {
  readonly kind: "terminal_cleanup"
  readonly driverId: string | null
  readonly result: TerminalCleanupResultV1 | null
  readonly unsupportedCase?: UnsupportedCaseV1
}

export type ExecutionWorldOperationResultV1 =
  | ExecutionWorldSandboxResultV1
  | ExecutionWorldTerminalStartResultV1
  | ExecutionWorldTerminalInteractionResultV1
  | ExecutionWorldTerminalSnapshotResultV1
  | ExecutionWorldTerminalCleanupResultV1

export interface ExecutionWorldSelectionV1 {
  readonly driverId: string | null
}

export interface ExecutionWorldV1 {
  select(input: {
    capability: ExecutionCapabilityV1
    placement: ExecutionPlacementV1
    terminal?: boolean
    driverIdHint?: ExecutionDriverHintV1
  }): ExecutionWorldSelectionV1
  execute(operation: ExecutionWorldOperationV1): Promise<ExecutionWorldOperationResultV1>
}

const DRIVER_ORDER: Record<ExecutionDriverHintV1, string[]> = {
  trusted_local: ["local-process", "oci", "remote"],
  oci: ["oci", "local-process", "remote"],
  remote: ["remote", "oci", "local-process"],
}

function orderDrivers<T extends ExecutionDriverV1>(drivers: readonly T[], hint?: ExecutionDriverHintV1): T[] {
  const preferred = DRIVER_ORDER[hint ?? "trusted_local"]
  const rank = new Map(preferred.map((driverId, index) => [driverId, index]))
  return drivers
    .map((driver, index) => ({ driver, index }))
    .sort((left, right) => {
      const leftRank = rank.get(left.driver.driverId) ?? preferred.length + left.index
      const rightRank = rank.get(right.driver.driverId) ?? preferred.length + right.index
      return leftRank - rightRank || left.index - right.index
    })
    .map(({ driver }) => driver)
}

function selectWorldDriver(
  drivers: readonly TerminalSessionDriverV1[],
  input: {
    capability: ExecutionCapabilityV1
    placement: ExecutionPlacementV1
    terminal?: boolean
    driverIdHint?: ExecutionDriverHintV1
  },
): TerminalSessionDriverV1 | null {
  const orderedDrivers = orderDrivers(drivers, input.driverIdHint)
  return input.terminal
    ? selectTerminalSessionDriver({
        capability: input.capability,
        placement: input.placement,
        drivers: orderedDrivers,
      })
    : (selectExecutionDriver({
        capability: input.capability,
        placement: input.placement,
        drivers: orderedDrivers,
      }) as TerminalSessionDriverV1 | null)
}

function buildLivenessEvidence(input: {
  requestId: string
  state: ExecutionLivenessEvidenceV1["state"]
  executionStarted: boolean
  terminationRequested?: boolean
  terminationObserved?: boolean
}): ExecutionLivenessEvidenceV1 {
  return {
    observed: input.executionStarted,
    executionStarted: input.executionStarted,
    state: input.state,
    terminationRequested: input.terminationRequested ?? false,
    terminationObserved: input.terminationObserved ?? false,
    observedAtUtc: new Date().toISOString(),
    evidenceRefs: input.executionStarted ? [`liveness://${input.requestId}`] : [],
  }
}

function buildSandboxFailureResult(input: {
  request: SandboxRequestV1
  status: SandboxResultV1["status"]
  message: string
  reason: string
  previous?: SandboxResultV1 | null
}): SandboxResultV1 {
  return assertValid<SandboxResultV1>("sandboxResult", {
    schema_version: "bb.sandbox_result.v1",
    request_id: input.request.request_id,
    status: input.status,
    placement_id: input.previous?.placement_id ?? null,
    stdout_ref: input.previous?.stdout_ref ?? null,
    stderr_ref: input.previous?.stderr_ref ?? null,
    artifact_refs: input.previous?.artifact_refs ?? [],
    side_effect_digest: input.previous?.side_effect_digest ?? null,
    usage: input.previous?.usage ?? null,
    evidence_refs: input.previous?.evidence_refs ?? [],
    error: {
      message: input.message,
      reason: input.reason,
      ...(input.previous?.error ?? {}),
    },
  })
}


async function settleWithin(promise: Promise<unknown>, timeoutMs: number): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<false>((resolve) => {
    timer = setTimeout(() => resolve(false), Math.max(0, timeoutMs))
  })
  try {
    return await Promise.race([promise.then(() => true, () => false), timeout])
  } finally {
    clearTimeout(timer)
  }
}

function buildTerminalUnsupportedCase(
  capability: ExecutionCapabilityV1,
  placement: ExecutionPlacementV1,
  summary: string,
  reasonCode = "unsupported_terminal_driver",
  metadata: Record<string, unknown> = {},
) {
  return buildExecutionDriverUnsupportedCase({
    capability,
    placementClass: placement.placement_class,
    summary,
    reasonCode,
    metadata: { reason_code: reasonCode, ...metadata },
  })
}

export function createExecutionWorld(input: {
  drivers: readonly TerminalSessionDriverV1[]
  defaultDeadlineMs?: number | null
  terminationGraceMs?: number
}): ExecutionWorldV1 {
  const drivers = [...input.drivers]
  const defaultDeadlineMs = input.defaultDeadlineMs ?? null
  const defaultTerminationGraceMs = input.terminationGraceMs ?? 2000
  const sessions = new Map<string, TerminalSessionDriverV1>()

  async function executeSandbox(operation: ExecutionWorldSandboxInputV1): Promise<ExecutionWorldSandboxResultV1> {
    const driver = selectWorldDriver(drivers, {
      capability: operation.capability,
      placement: operation.placement,
      driverIdHint: operation.driverIdHint,
    })
    if (!driver) {
      return {
        kind: "sandbox",
        driverId: null,
        plan: null,
        sandboxResult: null,
        livenessEvidence: buildLivenessEvidence({
          requestId: operation.requestId,
          state: "unsupported",
          executionStarted: false,
        }),
        unsupportedCase: buildExecutionDriverUnsupportedCase({
          capability: operation.capability,
          placementClass: operation.placement.placement_class,
        }),
      }
    }

    let plan: PlannedExecutionV1 | null
    try {
      plan = buildPlannedExecution({
        capability: operation.capability,
        placement: operation.placement,
        drivers: [driver],
        requestId: operation.requestId,
        command: operation.command,
        workspaceRef: operation.workspaceRef ?? null,
        imageRef: operation.imageRef ?? null,
        metadata: operation.metadata ?? {},
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : "Execution driver could not build a sandbox request"
      return {
        kind: "sandbox",
        driverId: driver.driverId,
        plan: null,
        sandboxResult: null,
        livenessEvidence: buildLivenessEvidence({
          requestId: operation.requestId,
          state: "unsupported",
          executionStarted: false,
        }),
        unsupportedCase: buildExecutionDriverUnsupportedCase({
          capability: operation.capability,
          placementClass: operation.placement.placement_class,
          summary: message,
        }),
      }
    }
    if (!plan?.sandboxRequest || typeof driver.execute !== "function") {
      return {
        kind: "sandbox",
        driverId: driver.driverId,
        plan,
        sandboxResult: null,
        livenessEvidence: buildLivenessEvidence({
          requestId: operation.requestId,
          state: "unsupported",
          executionStarted: false,
        }),
        unsupportedCase: buildExecutionDriverUnsupportedCase({
          capability: operation.capability,
          placementClass: operation.placement.placement_class,
          summary: `Execution driver ${driver.driverId} has no executable backend for ${operation.placement.placement_class}`,
        }),
      }
    }

    const request = plan.sandboxRequest

    if (operation.signal?.aborted) {
      const livenessEvidence = buildLivenessEvidence({
        requestId: request.request_id,
        state: "cancelled",
        executionStarted: false,
        terminationRequested: false,
        terminationObserved: false,
      })
      const sandboxResult = assertValid<SandboxResultV1>(
        "sandboxResult",
        buildSandboxFailureResult({
          request,
          status: "cancelled",
          message: "Execution was cancelled before starting",
          reason: "execution_cancelled",
        }),
      )
      return {
        kind: "sandbox",
        driverId: driver.driverId,
        plan,
        sandboxResult,
        livenessEvidence,
      }
    }
    const deadlineMs = operation.deadlineMs ?? defaultDeadlineMs
    if (deadlineMs != null && deadlineMs <= 0) {
      const livenessEvidence = buildLivenessEvidence({
        requestId: request.request_id,
        state: "timed_out",
        executionStarted: false,
        terminationRequested: false,
        terminationObserved: false,
      })
      if (operation.onTimeout) {
        try {
          void Promise.resolve(
            operation.onTimeout({ driverId: driver.driverId, request, liveness: livenessEvidence }),
          ).catch(() => {})
        } catch {
          // Timeout notification is advisory and cannot strand termination.
        }
      }
      const sandboxResult = assertValid<SandboxResultV1>(
        "sandboxResult",
        buildSandboxFailureResult({
          request,
          status: "timed_out",
          message: "Execution exceeded deadline before starting",
          reason: "deadline_exceeded",
        }),
      )
      return {
        kind: "sandbox",
        driverId: driver.driverId,
        plan,
        sandboxResult,
        livenessEvidence,
      }
    }

    const controller = new AbortController()
    const deadlineAtMs = deadlineMs == null ? null : Date.now() + Math.max(0, deadlineMs)
    const terminationGraceMs = Math.max(1, operation.terminationGraceMs ?? defaultTerminationGraceMs)
    let executionStarted = false
    let terminalized = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let externalAbortListener: (() => void) | undefined
    let resolveTerminal:
      | ((outcome: {
          status: "completed" | "failed" | "cancelled" | "timed_out"
          result?: SandboxResultV1
          error?: unknown
          executionStarted?: boolean
          terminationRequested?: boolean
          terminationObserved?: boolean
        }) => void)
      | undefined

    const terminalPromise = new Promise<{
      status: "completed" | "failed" | "cancelled" | "timed_out"
      result?: SandboxResultV1
      error?: unknown
      executionStarted?: boolean
      terminationRequested?: boolean
      terminationObserved?: boolean
    }>((resolve) => {
      resolveTerminal = resolve
    })

    const requestTermination = async (reason: "deadline" | "cancelled"): Promise<void> => {
      if (terminalized) return
      terminalized = true
      controller.abort(new Error(reason === "deadline" ? "execution deadline exceeded" : "execution cancelled"))
      let terminationObserved = false
      if (executionStarted && driver.terminate) {
        let terminationPromise: Promise<unknown>
        try {
          terminationPromise = Promise.resolve(
            driver.terminate(request, {
              reason,
              signal: controller.signal,
              deadlineAtMs,
            }),
          )
        } catch {
          terminationPromise = Promise.reject(new Error("synchronous terminate failure"))
        }
        terminationObserved = await settleWithin(terminationPromise, terminationGraceMs)
      }
      const liveness = buildLivenessEvidence({
        requestId: request.request_id,
        state: reason === "deadline" ? "timed_out" : "cancelled",
        executionStarted,
        terminationRequested: executionStarted,
        terminationObserved,
      })
      if (reason === "deadline" && operation.onTimeout) {
        try {
          void Promise.resolve(operation.onTimeout({ driverId: driver.driverId, request, liveness })).catch(() => {})
        } catch {
          // Timeout notification is advisory and cannot strand termination.
        }
      }
      resolveTerminal?.({
        status: reason === "deadline" ? "timed_out" : "cancelled",
        executionStarted,
        terminationRequested: executionStarted,
        terminationObserved,
      })
    }

    const executePromise = Promise.resolve()
      .then(() => {
        if (terminalized) return
        executionStarted = true
        return driver.execute!(request, {
          signal: controller.signal,
          deadlineAtMs,
          terminationGraceMs,
          capability: operation.capability,
          placement: operation.placement,
          driverId: driver.driverId,
        })
      })
      .then(
        (result) => {
          if (!terminalized) {
            terminalized = true
            if (result) {
              resolveTerminal?.({ status: result.status, result, executionStarted: true })
            } else {
              resolveTerminal?.({
                status: "failed",
                error: new Error("Execution driver returned null or undefined result"),
                executionStarted: true,
              })
            }
          }
        },
        (error: unknown) => {
          if (!terminalized) {
            terminalized = true
            resolveTerminal?.({ status: "failed", error, executionStarted: true })
          }
        },
      )

    if (deadlineMs != null) {
      timer = setTimeout(() => void requestTermination("deadline"), Math.max(0, deadlineMs))
    }
    if (operation.signal) {
      if (operation.signal.aborted) {
        void requestTermination("cancelled")
      } else {
        externalAbortListener = () => void requestTermination("cancelled")
        operation.signal.addEventListener("abort", externalAbortListener, { once: true })
      }
    }

    const terminal = await terminalPromise
    if (terminal.status === "completed" || terminal.status === "failed") {
      await executePromise
    }
    if (timer !== undefined) clearTimeout(timer)
    if (externalAbortListener && operation.signal) operation.signal.removeEventListener("abort", externalAbortListener)

    const status = terminal.status
    const livenessEvidence = buildLivenessEvidence({
      requestId: request.request_id,
      state: status,
      executionStarted: terminal.executionStarted ?? executionStarted,
      terminationRequested: terminal.terminationRequested,
      terminationObserved: terminal.terminationObserved,
    })
    let sandboxResult: SandboxResultV1
    try {
      const candidate = terminal.result
        ? status === "timed_out"
          ? buildSandboxFailureResult({
              request,
              status: "timed_out",
              message: "Execution exceeded its deadline",
              reason: "deadline_exceeded",
              previous: terminal.result,
            })
          : status === "cancelled"
            ? buildSandboxFailureResult({
                request,
                status: "cancelled",
                message: "Execution was cancelled",
                reason: "execution_cancelled",
                previous: terminal.result,
              })
            : assertValid<SandboxResultV1>("sandboxResult", terminal.result)
        : buildSandboxFailureResult({
            request,
            status,
            message:
              status === "timed_out"
                ? "Execution exceeded its deadline"
                : status === "cancelled"
                  ? "Execution was cancelled"
                  : terminal.error instanceof Error
                    ? terminal.error.message
                    : "Execution driver failed",
            reason:
              status === "timed_out"
                ? "deadline_exceeded"
                : status === "cancelled"
                  ? "execution_cancelled"
                  : "adapter_execution_failed",
          })
      sandboxResult = assertValid<SandboxResultV1>("sandboxResult", candidate)
    } catch (validationError: unknown) {
      sandboxResult = assertValid<SandboxResultV1>(
        "sandboxResult",
        buildSandboxFailureResult({
          request,
          status: "failed",
          message:
            validationError instanceof Error
              ? `Execution driver returned malformed result: ${validationError.message}`
              : "Execution driver returned malformed result",
          reason: "malformed_adapter_result",
        }),
      )
    }

    return {
      kind: "sandbox",
      driverId: driver.driverId,
      plan,
      sandboxResult,
      livenessEvidence,
    }
  }

  async function executeTerminalStart(operation: ExecutionWorldTerminalStartOperationV1): Promise<ExecutionWorldTerminalStartResultV1> {
    const driver = selectWorldDriver(drivers, {
      capability: operation.capability,
      placement: operation.placement,
      terminal: true,
      driverIdHint: operation.driverIdHint,
    })
    if (!driver?.startTerminalSession) {
      return {
        kind: "terminal_start",
        driverId: driver?.driverId ?? null,
        capability: operation.capability,
        placement: operation.placement,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          `No terminal start driver available for ${operation.placement.placement_class}.`,
        ),
      }
    }
    try {
      const rawResult = await driver.startTerminalSession(operation.input)
      if (!rawResult || !rawResult.descriptor) {
        throw new Error("Terminal start driver returned null or invalid result")
      }
      const descriptor = assertValid<TerminalSessionDescriptorV1>(
        "terminalSessionDescriptor",
        rawResult.descriptor,
      )
      const outputDeltas = (rawResult.outputDeltas ?? []).map((delta) =>
        assertValid<TerminalOutputDeltaV1>("terminalOutputDelta", delta),
      )
      const result: TerminalSessionStartResultV1 = {
        descriptor,
        outputDeltas,
      }
      sessions.set(operation.input.terminalSessionId, driver)
      return {
        kind: "terminal_start",
        driverId: driver.driverId,
        capability: operation.capability,
        placement: operation.placement,
        result,
      }
    } catch (error) {
      return {
        kind: "terminal_start",
        driverId: driver.driverId,
        capability: operation.capability,
        placement: operation.placement,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          error instanceof Error ? error.message : "Terminal start failed",
          "terminal_start_failed",
        ),
      }
    }
  }

  async function executeTerminalInteract(operation: ExecutionWorldTerminalInteractionOperationV1): Promise<ExecutionWorldTerminalInteractionResultV1> {
    const selected = sessions.get(operation.input.terminalSessionId)
    const driver =
      selected ??
      selectWorldDriver(drivers, {
        capability: operation.capability,
        placement: operation.placement,
        terminal: true,
        driverIdHint: operation.driverIdHint,
      })
    if (!driver?.interactTerminalSession) {
      return {
        kind: "terminal_interact",
        driverId: driver?.driverId ?? null,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          `No terminal interaction driver available for ${operation.input.terminalSessionId}.`,
        ),
      }
    }
    try {
      const rawResult = await driver.interactTerminalSession(operation.input)
      if (!rawResult || !rawResult.interaction) {
        throw new Error("Terminal interaction driver returned null or invalid result")
      }
      const interaction = assertValid<TerminalInteractionV1>("terminalInteraction", rawResult.interaction)
      const outputDeltas = (rawResult.outputDeltas ?? []).map((delta) =>
        assertValid<TerminalOutputDeltaV1>("terminalOutputDelta", delta),
      )
      const end = rawResult.end
        ? assertValid<TerminalSessionEndV1>("terminalSessionEnd", rawResult.end)
        : undefined
      const result: TerminalSessionInteractionResultV1 = {
        interaction,
        outputDeltas,
        ...(end ? { end } : {}),
      }
      return { kind: "terminal_interact", driverId: driver.driverId, result }
    } catch (error) {
      return {
        kind: "terminal_interact",
        driverId: driver.driverId,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          error instanceof Error ? error.message : `Terminal interaction failed for ${operation.input.terminalSessionId}.`,
          "terminal_interaction_failed",
          {
            terminal_session_id: operation.input.terminalSessionId,
            interaction_kind: operation.input.interactionKind,
          },
        ),
      }
    }
  }

  async function executeTerminalSnapshot(operation: ExecutionWorldTerminalSnapshotOperationV1): Promise<ExecutionWorldTerminalSnapshotResultV1> {
    const driver = selectWorldDriver(drivers, {
      capability: operation.capability,
      placement: operation.placement,
      terminal: true,
      driverIdHint: operation.driverIdHint,
    })
    if (!driver?.snapshotTerminalRegistry) {
      return {
        kind: "terminal_snapshot",
        driverId: driver?.driverId ?? null,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          `No terminal snapshot driver available for ${operation.placement.placement_class}.`,
        ),
      }
    }
    try {
      const rawResult = await driver.snapshotTerminalRegistry()
      if (!rawResult) {
        throw new Error("Terminal snapshot driver returned null or invalid result")
      }
      const result = assertValid<TerminalRegistrySnapshotV1>("terminalRegistrySnapshot", rawResult)
      return { kind: "terminal_snapshot", driverId: driver.driverId, result }
    } catch (error) {
      return {
        kind: "terminal_snapshot",
        driverId: driver.driverId,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          error instanceof Error ? error.message : "Terminal snapshot failed",
          "terminal_snapshot_failed",
        ),
      }
    }
  }

  async function executeTerminalCleanup(operation: ExecutionWorldTerminalCleanupOperationV1): Promise<ExecutionWorldTerminalCleanupResultV1> {
    const cleanupDrivers = drivers.filter(
      (d): d is TerminalSessionDriverV1 & { cleanupTerminalSessions: NonNullable<TerminalSessionDriverV1["cleanupTerminalSessions"]> } =>
        typeof d.cleanupTerminalSessions === "function",
    )
    if (cleanupDrivers.length === 0) {
      return {
        kind: "terminal_cleanup",
        driverId: null,
        result: null,
        unsupportedCase: buildTerminalUnsupportedCase(
          operation.capability,
          operation.placement,
          `No terminal cleanup driver available for ${operation.placement.placement_class}.`,
        ),
      }
    }

    if (operation.input.scope === "all") {
      const cleanedSet = new Set<string>()
      const failedSet = new Set<string>()
      for (const [id, owner] of sessions.entries()) {
        if (typeof owner.cleanupTerminalSessions !== "function") {
          failedSet.add(id)
        }
      }
      for (const d of cleanupDrivers) {
        try {
          const rawRes = await d.cleanupTerminalSessions(operation.input)
          const res = assertValid<TerminalCleanupResultV1>("terminalCleanupResult", rawRes)
          for (const id of res.cleaned_session_ids) {
            cleanedSet.add(id)
            sessions.delete(id)
          }
          if (res.failed_session_ids) {
            for (const id of res.failed_session_ids) failedSet.add(id)
          }
        } catch {
          for (const [id, owner] of sessions.entries()) {
            if (owner === d || owner.driverId === d.driverId) {
              failedSet.add(id)
            }
          }
        }
      }
      return {
        kind: "terminal_cleanup",
        driverId: cleanupDrivers[0]?.driverId ?? null,
        result: {
          schema_version: "bb.terminal_cleanup_result.v1",
          cleanup_id: operation.input.cleanupId,
          scope: "all",
          cleaned_session_ids: Array.from(cleanedSet),
          failed_session_ids: Array.from(failedSet),
        },
      }
    }

    const requestedIds =
      operation.input.scope === "single"
        ? (operation.input.sessionIds?.slice(0, 1) ?? [])
        : (operation.input.sessionIds ?? [])
    const defaultDriver =
      selectWorldDriver(drivers, {
        capability: operation.capability,
        placement: operation.placement,
        terminal: true,
        driverIdHint: operation.driverIdHint,
      }) ?? cleanupDrivers[0]!

    // Group session IDs by owning driver
    const driverToSessions = new Map<TerminalSessionDriverV1, string[]>()
    for (const sessionId of requestedIds) {
      const owner = sessions.get(sessionId) ?? defaultDriver
      const list = driverToSessions.get(owner) ?? []
      list.push(sessionId)
      driverToSessions.set(owner, list)
    }

    const cleanedSet = new Set<string>()
    const failedSet = new Set<string>()
    let primaryDriverId: string | null = null

    for (const [ownerDriver, sessionIds] of driverToSessions.entries()) {
      primaryDriverId = ownerDriver.driverId
      if (!ownerDriver.cleanupTerminalSessions) {
        for (const id of sessionIds) {
          failedSet.add(id)
        }
        continue
      }
      try {
        const rawRes = await ownerDriver.cleanupTerminalSessions({
          cleanupId: operation.input.cleanupId,
          scope: operation.input.scope === "single" ? "single" : "filtered",
          sessionIds,
          signal: operation.input.signal,
        })
        const res = assertValid<TerminalCleanupResultV1>("terminalCleanupResult", rawRes)
        for (const id of res.cleaned_session_ids) {
          cleanedSet.add(id)
          sessions.delete(id)
        }
        if (res.failed_session_ids) {
          for (const id of res.failed_session_ids) failedSet.add(id)
        }
      } catch {
        for (const id of sessionIds) failedSet.add(id)
      }
    }

    return {
      kind: "terminal_cleanup",
      driverId: primaryDriverId,
      result: {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: operation.input.cleanupId,
        scope: operation.input.scope,
        cleaned_session_ids: Array.from(cleanedSet),
        failed_session_ids: Array.from(failedSet),
      },
    }
  }

  return {
    select(selection) {
      return {
        driverId:
          selectWorldDriver(drivers, {
            capability: selection.capability,
            placement: selection.placement,
            terminal: selection.terminal,
            driverIdHint: selection.driverIdHint,
          })?.driverId ?? null,
      }
    },
    async execute(operation) {
      switch (operation.kind) {
        case "sandbox":
          return executeSandbox(operation)
        case "terminal_start":
          return executeTerminalStart(operation)
        case "terminal_interact":
          return executeTerminalInteract(operation)
        case "terminal_snapshot":
          return executeTerminalSnapshot(operation)
        case "terminal_cleanup":
          return executeTerminalCleanup(operation)
      }
    },
  }
}

export function chooseExecutionPlacementClass(
  capability: ExecutionCapabilityV1,
  driverIdHint?: ExecutionDriverHintV1,
): ExecutionPlacementV1["placement_class"] {
  if (driverIdHint === "remote" || capability.isolation_class === "remote_service") {
    switch (capability.isolation_class) {
      case "remote_service":
        return "remote_worker"
      case "microvm":
        return "delegated_microvm"
      case "oci":
      case "gvisor":
      case "kata":
        return "delegated_oci"
      default:
        return "delegated_python"
    }
  }
  if (driverIdHint === "oci" || ["oci", "gvisor", "kata"].includes(capability.isolation_class)) {
    switch (capability.isolation_class) {
      case "gvisor":
        return "local_oci_gvisor"
      case "kata":
        return "local_oci_kata"
      default:
        return "local_oci"
    }
  }
  return capability.isolation_class === "none" ? "inline_ts" : "local_process"
}
