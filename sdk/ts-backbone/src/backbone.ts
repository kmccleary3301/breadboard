import {
  executeDriverMediatedToolTurn,
  executeProviderTextContinuationTurn,
  executeProviderTextTurn,
} from "@breadboard/kernel-core"
import type { Backbone, BackboneOptions, BackboneSession, HostSessionDescriptor, ProviderTurnInput, ToolTurnInput } from "./types.js"
import {
  buildBackboneTurnResult,
  buildCoordinationInspectionSnapshot,
  buildProjectionProfile,
  buildSupportClaim,
  buildToolTurnSupportClaim,
} from "./support.js"

function makeBackboneSession(options: BackboneOptions, descriptor: HostSessionDescriptor): BackboneSession {
  const projectionProfile = buildProjectionProfile(
    descriptor.projectionProfileId ?? options.defaultProjectionProfileId ?? "host_callbacks",
  )
  let coordinationInspection = buildCoordinationInspectionSnapshot()
  return {
    descriptor,
    workspace: options.workspace,
    projectionProfile,
    classifyProviderTurn(input: ProviderTurnInput) {
      return buildSupportClaim({
        workspace: options.workspace,
        request: input.request,
        executionProfileId: options.workspace.defaultExecutionProfileId,
        summary: `Provider-backed text turn supported on ${options.workspace.defaultExecutionProfileId}.`,
      })
    },
    classifyToolTurn(input: ToolTurnInput) {
      return buildToolTurnSupportClaim(options.workspace, input)
    },
    async runProviderTurn(input: ProviderTurnInput) {
      const supportClaim = buildSupportClaim({
        workspace: options.workspace,
        request: input.request,
        executionProfileId: options.workspace.defaultExecutionProfileId,
        summary: `Provider-backed text turn supported on ${options.workspace.defaultExecutionProfileId}.`,
      })
      const providerTurn = input.existingTranscript
        ? executeProviderTextContinuationTurn(input.request, {
            sessionId: descriptor.sessionId,
            providerExchange: input.providerExchange,
            assistantText: input.assistantText,
            existingTranscript: input.existingTranscript,
          })
        : executeProviderTextTurn(input.request, {
            sessionId: descriptor.sessionId,
            providerExchange: input.providerExchange,
            assistantText: input.assistantText,
          })
      const result = buildBackboneTurnResult({
        supportClaim,
        projectionProfile,
        runContextId: providerTurn.runContext.request_id,
        transcript: providerTurn.transcript,
        events: providerTurn.events,
        providerTurn,
      })
      coordinationInspection = result.coordinationInspection
      return result
    },
    async runToolTurn(input: ToolTurnInput) {
      const supportClaim = buildToolTurnSupportClaim(options.workspace, input)
      const driverIdHint = input.driverIdHint ??
        (supportClaim.executionProfileId === "remote_isolated"
          ? "remote"
          : supportClaim.executionProfileId === "sandboxed_local"
            ? "oci"
            : "trusted_local")
      const driverTurn = await executeDriverMediatedToolTurn(input.request, {
        sessionId: descriptor.sessionId,
        toolName: input.toolName,
        command: input.command,
        driverIdHint,
        assistantText: input.assistantText ?? null,
        workspaceRef: descriptor.workspaceRoot ?? options.workspace.rootDir ?? null,
      })
      const result = buildBackboneTurnResult({
        supportClaim,
        projectionProfile,
        runContextId: driverTurn.runContext.request_id,
        transcript: driverTurn.transcript,
        events: driverTurn.events,
        driverTurn,
      })
      coordinationInspection = result.coordinationInspection
      return result
    },
    inspectCoordination(input) {
      coordinationInspection = buildCoordinationInspectionSnapshot({
        snapshot: input?.events ? input?.snapshot : (input?.snapshot ?? coordinationInspection),
        events: input?.events,
      })
      return coordinationInspection
    },
  }
}

export function createBackbone(options: BackboneOptions): Backbone {
  return {
    workspace: options.workspace,
    openSession(descriptor: HostSessionDescriptor): BackboneSession {
      return makeBackboneSession(options, descriptor)
    },
  }
}
