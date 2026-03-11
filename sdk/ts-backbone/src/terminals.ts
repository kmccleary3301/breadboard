import { randomUUID } from "node:crypto"

import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  TerminalCleanupResultV1,
  TerminalInteractionV1,
  TerminalOutputDeltaV1,
  TerminalRegistrySnapshotV1,
  TerminalSessionDescriptorV1,
  UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"
import { assertValid } from "@breadboard/kernel-contracts"
import {
  selectTerminalSessionDriver,
  type TerminalSessionCleanupInputV1,
  type TerminalSessionDriverV1,
  type TerminalSessionInteractionInputV1,
  type TerminalSessionInteractionResultV1,
  type TerminalSessionStartInputV1,
  type TerminalSessionStartResultV1,
} from "@breadboard/execution-drivers"
import { buildTerminalCleanupResult, reduceTerminalRegistry } from "@breadboard/kernel-core"
import { buildExecutionPlacement } from "@breadboard/kernel-core"
import { chooseTrustedLocalPlacement, trustedLocalExecutionDriver } from "@breadboard/execution-driver-local"
import { chooseOciPlacement, makeOciExecutionDriver, type OciTerminalSessionAdapter } from "@breadboard/execution-driver-oci"
import {
  chooseRemotePlacement,
  makeRemoteExecutionDriver,
  type RemoteExecutionHttpOptions,
  type RemoteSandboxExecutor,
} from "@breadboard/execution-driver-remote"
import type { ExecutionProfileId, Workspace } from "@breadboard/workspace"
import type {
  BackboneTerminalApi,
  BackboneTerminalCleanupInput,
  BackboneTerminalCleanupResult,
  BackboneTerminalInteractionInput,
  BackboneTerminalInteractionResult,
  BackboneTerminalSessionView,
  BackboneTerminalStartInput,
  BackboneTerminalStartResult,
  SupportClaim,
} from "./types.js"
import { buildSupportClaim } from "./support.js"

function decodeTerminalOutputText(outputDeltas: readonly TerminalOutputDeltaV1[]): string {
  return outputDeltas
    .map((delta) => Buffer.from(delta.chunk_b64, "base64").toString("utf8"))
    .join("")
}

function createTerminalSessionView(options: {
  api: BackboneTerminalApi
  descriptor: TerminalSessionDescriptorV1
  supportClaim: SupportClaim
  executionProfileId: ExecutionProfileId
  workspace: Workspace
  initialOutputDeltas?: readonly TerminalOutputDeltaV1[]
  initialEnd?: import("@breadboard/kernel-contracts").TerminalSessionEndV1 | undefined
}): BackboneTerminalSessionView {
  let lastSnapshot: TerminalRegistrySnapshotV1 | null = null
  let lastEnd = options.initialEnd ?? null
  let outputText = decodeTerminalOutputText(options.initialOutputDeltas ?? [])
  let outputChunkCount = options.initialOutputDeltas?.length ?? 0

  function appendOutput(outputDeltas: readonly TerminalOutputDeltaV1[]): void {
    if (outputDeltas.length === 0) return
    outputText += decodeTerminalOutputText(outputDeltas)
    outputChunkCount += outputDeltas.length
  }

  function applySnapshot(snapshot: TerminalRegistrySnapshotV1 | null): void {
    lastSnapshot = snapshot
  }

  function buildSummary() {
    const status: "running" | "ended" = lastEnd ? "ended" : "running"
    const outputPreview =
      outputText.length === 0
        ? ""
        : options.workspace.shapeTerminalOutput(outputText, {
            chunkCount: outputChunkCount,
          }).userVisibleText
    return {
      terminalSessionId: options.descriptor.terminal_session_id,
      commandSummary: options.descriptor.command.join(" "),
      status,
      publicHandles: [...(options.descriptor.public_handles ?? [])],
      outputPreview,
      outputChunkCount,
      persistenceScope: options.descriptor.persistence_scope,
      continuationScope: options.descriptor.continuation_scope,
      lastSnapshotId: lastSnapshot?.snapshot_id ?? null,
      lastEndState: lastEnd?.terminal_state ?? null,
      exitCode: lastEnd?.exit_code ?? null,
      durationMs: lastEnd?.duration_ms ?? null,
      artifactRefCount: lastEnd?.artifact_refs?.length ?? 0,
      evidenceRefCount: lastEnd?.evidence_refs?.length ?? 0,
    }
  }

  return {
    descriptor: options.descriptor,
    supportClaim: options.supportClaim,
    executionProfileId: options.executionProfileId,
    get status() {
      return lastEnd ? "ended" : "running"
    },
    get lastSnapshot() {
      return lastSnapshot
    },
    get lastEnd() {
      return lastEnd
    },
    summary() {
      return buildSummary()
    },
    async refresh() {
      const result = await options.api.snapshot({
        executionProfileId: options.executionProfileId,
      })
      applySnapshot(result.snapshot)
      if (result.snapshot?.ended_session_ids?.includes(options.descriptor.terminal_session_id)) {
        lastEnd =
          lastEnd ??
          ({
            schema_version: "bb.terminal_session_end.v1",
            terminal_session_id: options.descriptor.terminal_session_id,
            startup_call_id: options.descriptor.startup_call_id ?? null,
            causing_call_id: null,
            terminal_state: "completed",
            exit_code: null,
            duration_ms: 0,
            artifact_refs: [],
            evidence_refs: [],
          } as const)
      }
      return result
    },
    poll(input = {}) {
      return options.api.interact({
        terminalSessionId: options.descriptor.terminal_session_id,
        executionProfileId: options.executionProfileId,
        interactionKind: "poll",
        settleMs: input.settleMs,
        causingCallId: input.causingCallId ?? null,
      }).then((result) => {
        appendOutput(result.outputDeltas)
        if (result.end) {
          lastEnd = result.end
        }
        return result
      })
    },
    writeStdin(inputText, input = {}) {
      return options.api.interact({
        terminalSessionId: options.descriptor.terminal_session_id,
        executionProfileId: options.executionProfileId,
        interactionKind: "stdin",
        inputText,
        causingCallId: input.causingCallId ?? null,
        settleMs: input.settleMs,
      }).then((result) => {
        appendOutput(result.outputDeltas)
        if (result.end) {
          lastEnd = result.end
        }
        return result
      })
    },
    sendSignal(signal, input = {}) {
      return options.api.interact({
        terminalSessionId: options.descriptor.terminal_session_id,
        executionProfileId: options.executionProfileId,
        interactionKind: "signal",
        signal,
        causingCallId: input.causingCallId ?? null,
      }).then((result) => {
        appendOutput(result.outputDeltas)
        if (result.end) {
          lastEnd = result.end
        }
        return result
      })
    },
    snapshot() {
      return options.api.snapshot({
        executionProfileId: options.executionProfileId,
      }).then((result) => {
        applySnapshot(result.snapshot)
        return result
      })
    },
    cleanup(input = {}) {
      return options.api.cleanup({
        scope: "single",
        executionProfileId: options.executionProfileId,
        sessionIds: [options.descriptor.terminal_session_id],
        signal: input.signal ?? null,
      }).then((result) => {
        if (result.result?.cleaned_session_ids.includes(options.descriptor.terminal_session_id)) {
          lastEnd =
            lastEnd ??
            ({
              schema_version: "bb.terminal_session_end.v1",
              terminal_session_id: options.descriptor.terminal_session_id,
              startup_call_id: options.descriptor.startup_call_id ?? null,
              causing_call_id: null,
              terminal_state: "cleaned_up",
              exit_code: null,
              duration_ms: 0,
              artifact_refs: [],
              evidence_refs: [],
            } as const)
        }
        return result
      })
    },
  }
}

function buildTerminalCapability(input: {
  profileId: ExecutionProfileId
  terminalSessionId: string
  workspace: Workspace
}): ExecutionCapabilityV1 {
  const workspaceRoot = input.workspace.rootDir
  const sharedPaths = workspaceRoot ? [workspaceRoot] : []
  if (input.profileId === "remote_isolated") {
    return {
      schema_version: "bb.execution_capability.v1",
      capability_id: `term-cap:${input.terminalSessionId}`,
      security_tier: "multi_tenant",
      isolation_class: "remote_service",
      allow_read_paths: [],
      allow_write_paths: [],
      allow_net_hosts: [],
      allow_run_programs: [],
      allow_env_keys: [],
      secret_mode: "ref_only",
      tty_mode: "optional",
      resource_budget: null,
      evidence_mode: "audit_full",
    }
  }
  if (input.profileId === "sandboxed_local") {
    return {
      schema_version: "bb.execution_capability.v1",
      capability_id: `term-cap:${input.terminalSessionId}`,
      security_tier: "single_tenant",
      isolation_class: "oci",
      allow_read_paths: sharedPaths,
      allow_write_paths: sharedPaths,
      allow_net_hosts: [],
      allow_run_programs: [],
      allow_env_keys: [],
      secret_mode: "ref_only",
      tty_mode: "optional",
      resource_budget: null,
      evidence_mode: "replay_strict",
    }
  }
  return {
    schema_version: "bb.execution_capability.v1",
    capability_id: `term-cap:${input.terminalSessionId}`,
    security_tier: input.profileId === "constrained_local" ? "shared_host" : "trusted_dev",
    isolation_class: "process",
    allow_read_paths: sharedPaths,
    allow_write_paths: sharedPaths,
    allow_net_hosts: [],
    allow_run_programs: [],
    allow_env_keys: [],
    secret_mode: "ref_only",
    tty_mode: "optional",
    resource_budget: null,
    evidence_mode: "replay_strict",
  }
}

function chooseTerminalPlacement(capability: ExecutionCapabilityV1, profileId: ExecutionProfileId): ExecutionPlacementV1 {
  const placementClass =
    profileId === "remote_isolated"
      ? chooseRemotePlacement(capability)
      : profileId === "sandboxed_local"
        ? chooseOciPlacement(capability)
        : chooseTrustedLocalPlacement(capability)
  return buildExecutionPlacement(capability, {
    placementId: `term-place:${capability.capability_id}`,
    placementClass,
    runtimeId:
      profileId === "remote_isolated"
        ? "breadboard.ts-execution-driver-remote"
        : profileId === "sandboxed_local"
          ? "breadboard.ts-execution-driver-oci"
          : "breadboard.ts-execution-driver-local",
  })
}

function buildTerminalSupportClaim(options: {
  workspace: Workspace
  executionProfileId: ExecutionProfileId
  summary: string
  unsupportedFields?: readonly string[]
}): SupportClaim {
  return {
    ...buildSupportClaim({
      workspace: options.workspace,
      request: {
        schema_version: "bb.run_request.v1",
        request_id: `term-support:${options.executionProfileId}`,
        entry_mode: "terminal_session",
        task: options.summary,
        workspace_root: options.workspace.rootDir ?? null,
        requested_features: {},
        metadata: { surface: "terminal_session" },
      },
      executionProfileId: options.executionProfileId,
      summary: options.summary,
      recommendedHostMode: options.executionProfileId === "remote_isolated" ? "background" : "streaming",
    }),
    terminalSupport: {
      canStart: true,
      canInteract: true,
      canPoll: true,
      canList: true,
      canCleanup: true,
      streamMode: options.executionProfileId === "trusted_local" ? "pipes" : "pipes",
    },
    unsupportedFields: [...(options.unsupportedFields ?? [])],
  }
}

function buildUnsupportedTerminalCase(options: {
  profileId: ExecutionProfileId
  summary: string
}): UnsupportedCaseV1 {
  return assertValid<UnsupportedCaseV1>("unsupportedCase", {
    schema_version: "bb.unsupported_case.v1",
    reason_code: "unsupported_terminal_driver",
    summary: options.summary,
    contract_family: "bb.terminal_session_descriptor.v1",
    fallback_allowed: false,
    fallback_taken: false,
    evidence_refs: [],
    metadata: { execution_profile_id: options.profileId },
  })
}

function resolveTerminalDriver(options: {
  workspace: Workspace
  executionProfileId: ExecutionProfileId
  terminalSessionId?: string
  remoteExecutor?: RemoteSandboxExecutor
  remoteHttp?: RemoteExecutionHttpOptions
  ociTerminalAdapter?: OciTerminalSessionAdapter
}): {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  driver: TerminalSessionDriverV1 | null
  claim: SupportClaim
} {
  const capability = buildTerminalCapability({
    profileId: options.executionProfileId,
    terminalSessionId: options.terminalSessionId ?? randomUUID(),
    workspace: options.workspace,
  })
  const placement = chooseTerminalPlacement(capability, options.executionProfileId)
  const drivers: TerminalSessionDriverV1[] = [
    trustedLocalExecutionDriver,
    makeOciExecutionDriver(options.ociTerminalAdapter),
    makeRemoteExecutionDriver(options.remoteExecutor, options.remoteHttp),
  ]
  const driver = selectTerminalSessionDriver({
    capability,
    placement,
    drivers,
  })
  const summary = driver
    ? `Terminal sessions supported on ${options.executionProfileId} via ${driver.driverId}.`
    : `Terminal sessions are not supported on ${options.executionProfileId}.`
  return {
    capability,
    placement,
    driver,
    claim: buildTerminalSupportClaim({
      workspace: options.workspace,
      executionProfileId: options.executionProfileId,
      summary,
      unsupportedFields: driver ? [] : ["terminal_sessions"],
    }),
  }
}

export function createBackboneTerminalApi(options: {
  workspace: Workspace
  remoteExecutor?: RemoteSandboxExecutor
  remoteHttp?: RemoteExecutionHttpOptions
  ociTerminalAdapter?: OciTerminalSessionAdapter
}): BackboneTerminalApi {
  const api: BackboneTerminalApi = {
    reduceRegistry(events) {
      return reduceTerminalRegistry(events)
    },
    buildCleanupResult(input) {
      return buildTerminalCleanupResult(input)
    },
    classify(input) {
      return resolveTerminalDriver({
        workspace: options.workspace,
        executionProfileId: input.executionProfileId ?? options.workspace.defaultExecutionProfileId,
        terminalSessionId: "term-support",
        remoteExecutor: options.remoteExecutor,
        remoteHttp: options.remoteHttp,
        ociTerminalAdapter: options.ociTerminalAdapter,
      }).claim
    },
    async start(input) {
      const executionProfileId = input.executionProfileId ?? options.workspace.defaultExecutionProfileId
      const terminalSessionId = input.terminalSessionId ?? `term:${randomUUID()}`
      const resolved = resolveTerminalDriver({
        workspace: options.workspace,
        executionProfileId,
        terminalSessionId,
        remoteExecutor: options.remoteExecutor,
        remoteHttp: options.remoteHttp,
        ociTerminalAdapter: options.ociTerminalAdapter,
      })
      if (!resolved.driver?.startTerminalSession) {
        return {
          supportClaim: resolved.claim,
          unsupportedCase: buildUnsupportedTerminalCase({
            profileId: executionProfileId,
            summary: `No terminal driver available for ${executionProfileId}.`,
          }),
          descriptor: null,
          outputDeltas: [],
          session: null,
        }
      }
      const startInput: TerminalSessionStartInputV1 = {
        terminalSessionId,
        command: input.command,
        cwd: input.cwd ?? options.workspace.rootDir ?? null,
        startupCallId: input.startupCallId ?? null,
        ownerTaskId: input.ownerTaskId ?? null,
        publicHandles: input.publicHandles,
        capability: resolved.capability,
        placement: resolved.placement,
        persistenceScope: input.persistenceScope ?? "thread",
        continuationScope: input.continuationScope ?? "both",
        streamMode: input.streamMode ?? "pipes",
        streamSplit: input.streamSplit ?? "stdout_stderr",
      }
      const result = await resolved.driver.startTerminalSession(startInput)
      return {
        supportClaim: resolved.claim,
        descriptor: result.descriptor,
        outputDeltas: result.outputDeltas,
        end: result.end,
        session: createTerminalSessionView({
          api,
          descriptor: result.descriptor,
          supportClaim: resolved.claim,
          executionProfileId,
          workspace: options.workspace,
          initialOutputDeltas: result.outputDeltas,
          initialEnd: result.end,
        }),
      }
    },
    async interact(input) {
      const executionProfileId = input.executionProfileId ?? options.workspace.defaultExecutionProfileId
      const resolved = resolveTerminalDriver({
        workspace: options.workspace,
        executionProfileId,
        remoteExecutor: options.remoteExecutor,
        remoteHttp: options.remoteHttp,
        ociTerminalAdapter: options.ociTerminalAdapter,
      })
      if (!resolved.driver?.interactTerminalSession) {
        return {
          supportClaim: resolved.claim,
          unsupportedCase: buildUnsupportedTerminalCase({
            profileId: executionProfileId,
            summary: `No terminal interaction driver available for ${executionProfileId}.`,
          }),
          interaction: null,
          outputDeltas: [],
          end: undefined,
        }
      }
      const result = await resolved.driver.interactTerminalSession({
        terminalSessionId: input.terminalSessionId,
        interactionKind: input.interactionKind,
        causingCallId: input.causingCallId ?? null,
        inputText: input.inputText ?? null,
        inputB64: input.inputB64 ?? null,
        signal: input.signal ?? null,
        settleMs: input.settleMs,
      })
      return {
        supportClaim: resolved.claim,
        interaction: result.interaction,
        outputDeltas: result.outputDeltas,
        end: result.end,
      }
    },
    async snapshot(input) {
      const executionProfileId = input?.executionProfileId ?? options.workspace.defaultExecutionProfileId
      const resolved = resolveTerminalDriver({
        workspace: options.workspace,
        executionProfileId,
        remoteExecutor: options.remoteExecutor,
        remoteHttp: options.remoteHttp,
        ociTerminalAdapter: options.ociTerminalAdapter,
      })
      if (!resolved.driver?.snapshotTerminalRegistry) {
        return {
          supportClaim: resolved.claim,
          unsupportedCase: buildUnsupportedTerminalCase({
            profileId: executionProfileId,
            summary: `No terminal snapshot driver available for ${executionProfileId}.`,
          }),
          snapshot: null,
        }
      }
      return {
        supportClaim: resolved.claim,
        snapshot: await resolved.driver.snapshotTerminalRegistry(),
      }
    },
    async list(input) {
      return api.snapshot(input)
    },
    async cleanup(input) {
      const executionProfileId = input.executionProfileId ?? options.workspace.defaultExecutionProfileId
      const resolved = resolveTerminalDriver({
        workspace: options.workspace,
        executionProfileId,
        remoteExecutor: options.remoteExecutor,
        remoteHttp: options.remoteHttp,
        ociTerminalAdapter: options.ociTerminalAdapter,
      })
      if (!resolved.driver?.cleanupTerminalSessions) {
        return {
          supportClaim: resolved.claim,
          unsupportedCase: buildUnsupportedTerminalCase({
            profileId: executionProfileId,
            summary: `No terminal cleanup driver available for ${executionProfileId}.`,
          }),
          result: null,
        }
      }
      const cleanupInput: TerminalSessionCleanupInputV1 = {
        cleanupId: input.cleanupId ?? `term-cleanup:${randomUUID()}`,
        scope: input.scope,
        sessionIds: input.sessionIds,
        signal: input.signal ?? null,
      }
      return {
        supportClaim: resolved.claim,
        result: await resolved.driver.cleanupTerminalSessions(cleanupInput),
      }
    },
  }
  return api
}
