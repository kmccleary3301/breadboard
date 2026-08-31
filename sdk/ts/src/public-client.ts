import {
  ApiError,
  createBreadboardClient as createInternalBreadboardClient,
  type BreadboardClientConfig,
  type PublicActionId,
} from "./client.js"
import type { EventStreamOptions } from "./stream.js"
import type {
  PublicHarnessCreateRequest,
  PublicHarnessUpdateRequest,
  PublicResult,
  PublicSessionApprovalRequest,
  PublicSessionCancelRequest,
  PublicSessionDecision,
  PublicSessionInputRequest,
  PublicSessionStartRequest,
  SessionEvent,
} from "./types.js"

export { ApiError }
export type { BreadboardClientConfig, PublicActionId }

export interface BreadboardClient {
  invokePublicAction(
    actionId: PublicActionId,
    input?: Readonly<Record<string, unknown>>,
  ): Promise<unknown>
  describeSystem(): Promise<PublicResult>
  healthSystem(): Promise<PublicResult>
  schemasSystem(): Promise<PublicResult>
  createHarness(directory?: PublicHarnessCreateRequest["directory"]): Promise<PublicResult>
  listHarness(): Promise<PublicResult>
  getHarness(id: string): Promise<PublicResult>
  updateHarness(
    id: string,
    definition: PublicHarnessUpdateRequest["definition"],
  ): Promise<PublicResult>
  validateHarness(id: string): Promise<PublicResult>
  explainHarness(id: string): Promise<PublicResult>
  lockHarness(id: string): Promise<PublicResult>
  getHarnessLock(id: string): Promise<PublicResult>
  listIntegration(): Promise<PublicResult>
  getIntegration(id: string): Promise<PublicResult>
  probeIntegration(id: string, idempotencyKey?: string): Promise<PublicResult>
  listArtifact(): Promise<PublicResult>
  getArtifact(id: string): Promise<PublicResult>
  verifyArtifact(id: string): Promise<PublicResult>
  startSession(
    body: PublicSessionStartRequest,
    idempotencyKey?: string,
  ): Promise<PublicResult>
  listSession(): Promise<PublicResult>
  getSessionResult(id: string): Promise<PublicResult>
  sendInputSession(
    id: string,
    content: PublicSessionInputRequest["content"],
    idempotencyKey?: string,
  ): Promise<PublicResult>
  approveSession(
    id: string,
    requestId: PublicSessionApprovalRequest["request_id"],
    decision: PublicSessionDecision,
    idempotencyKey?: string,
  ): Promise<PublicResult>
  resumeSession(id: string, idempotencyKey?: string): Promise<PublicResult>
  cancelSession(
    id: string,
    reason?: PublicSessionCancelRequest["reason"],
    idempotencyKey?: string,
  ): Promise<PublicResult>
  eventsSession(
    id: string,
    options?: Omit<EventStreamOptions, "config">,
  ): AsyncGenerator<SessionEvent, void, void>
  artifactsSession(id: string): Promise<PublicResult>
}

export const createBreadboardClient = (
  config: BreadboardClientConfig,
): BreadboardClient => {
  const internal = createInternalBreadboardClient(config)
  return {
    invokePublicAction: internal.invokePublicAction,
    describeSystem: internal.describeSystem,
    healthSystem: internal.healthSystem,
    schemasSystem: internal.schemasSystem,
    createHarness: internal.createHarness,
    listHarness: internal.listHarness,
    getHarness: internal.getHarness,
    updateHarness: internal.updateHarness,
    validateHarness: internal.validateHarness,
    explainHarness: internal.explainHarness,
    lockHarness: internal.lockHarness,
    getHarnessLock: internal.getHarnessLock,
    listIntegration: internal.listIntegration,
    getIntegration: internal.getIntegration,
    probeIntegration: internal.probeIntegration,
    listArtifact: internal.listArtifact,
    getArtifact: internal.getArtifact,
    verifyArtifact: internal.verifyArtifact,
    startSession: internal.startSession,
    listSession: internal.listSession,
    getSessionResult: internal.getSessionResult,
    sendInputSession: internal.sendInputSession,
    approveSession: internal.approveSession,
    resumeSession: internal.resumeSession,
    cancelSession: internal.cancelSession,
    eventsSession: internal.eventsSession,
    artifactsSession: internal.artifactsSession,
  }
}

export const createApiClient = createBreadboardClient
export type { PublicResult }
