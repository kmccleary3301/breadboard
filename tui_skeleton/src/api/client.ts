import { loadAppConfig, type AppConfig } from "../config/appConfig.js"
import { resolveAuthToken } from "../config/authTokenProvider.js"
import type {
  ErrorResponse,
  SessionCreateRequest,
  SessionCreateResponse,
  SessionEvent,
  SessionSummary,
  SessionKernelRecordList,
  SessionFileInfo,
  SessionFileContent,
  SessionInputResponse,
  SessionCommandResponse,
  HealthResponse,
  EngineStatusResponse,
  ModelCatalogResponse,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
  CTreeTreeResponse,
  CTreeDiskArtifactsResponse,
  CTreeEventsResponse,
  ProviderAuthAttachRequest,
  ProviderAuthAttachResponse,
  ProviderAuthDetachRequest,
  ProviderAuthDetachResponse,
  ProviderAuthStatusResponse,
  E4CatalogPage,
  E4CatalogBinding,
  E4ClaimDetail,
  E4ClaimList,
  E4CoverageMatrix,
  E4ApiErrorEnvelope,
  E4Health,
  E4LaneDetail,
  E4LaneList,
  E4LedgerRows,
  E4RecordList,
  E4ReverifyRequest,
  E4ReverifyResult,
  E4SchemaList,
  RegistryList,
  RlRunSubmitRequest,
  RlRunStatus,
  RlStreamEvent,
  RlArtifact,
  RlAuditReport,
} from "./types.js"

export class ApiError extends Error {
  readonly status: number
  readonly body?: unknown

  constructor(message: string, status: number, body?: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.body = body
  }
}

type JsonMethod = "GET" | "POST" | "DELETE"

interface RequestOptions {
  body?: unknown
  query?: Record<string, string | number | boolean | undefined>
  responseType?: "json" | "text"
  headers?: Record<string, string>
}

export interface AttachmentUploadPayload {
  readonly mime: string
  readonly base64: string
  readonly size: number
  readonly filename?: string
}

export interface AttachmentHandle {
  readonly id: string
  readonly filename?: string
  readonly mime?: string
  readonly size_bytes?: number
}

export interface ReadSessionFileOptions {
  readonly mode?: "cat" | "snippet"
  readonly headLines?: number
  readonly tailLines?: number
  readonly maxBytes?: number
}

export interface ApiClientConfig {
  readonly baseUrl: string
  readonly authToken?: string | (() => Promise<string | undefined>)
  readonly requestTimeoutMs?: number
}

export interface ApiClientInstance {
  health(): Promise<HealthResponse>
  engineStatus(): Promise<EngineStatusResponse>
  createSession(payload: SessionCreateRequest): Promise<SessionCreateResponse>
  listSessions(): Promise<SessionSummary[]>
  getSession(sessionId: string): Promise<SessionSummary>
  listSessionRecords(sessionId: string, options?: { readonly schemaVersion?: string; readonly offset?: number; readonly limit?: number }): Promise<SessionKernelRecordList>
  postInput(sessionId: string, body: { content: string; attachments?: ReadonlyArray<string> }): Promise<SessionInputResponse>
  postCommand(sessionId: string, body: Record<string, unknown>): Promise<SessionCommandResponse>
  deleteSession(sessionId: string): Promise<void>
  listSessionFiles(sessionId: string, path?: string): Promise<SessionFileInfo[]>
  readSessionFile(sessionId: string, filePath: string, options?: ReadSessionFileOptions): Promise<SessionFileContent>
  getModelCatalog(configPath: string): Promise<ModelCatalogResponse>
  getSkillsCatalog(sessionId: string): Promise<SkillCatalogResponse>
  getCtreeSnapshot(sessionId: string): Promise<CTreeSnapshotResponse>
  getCtreeTree(sessionId: string, options?: { readonly stage?: string; readonly includePreviews?: boolean; readonly source?: string }): Promise<CTreeTreeResponse>
  getCtreeDisk(sessionId: string): Promise<CTreeDiskArtifactsResponse>
  getCtreeEvents(sessionId: string, options?: { readonly source?: string; readonly offset?: number; readonly limit?: number }): Promise<CTreeEventsResponse>
  getE4Health(): Promise<E4Health>
  listE4Schemas(): Promise<E4SchemaList>
  listE4Lanes(options?: { readonly phase?: string; readonly kind?: string; readonly targetFamily?: string; readonly status?: string; readonly offset?: number; readonly limit?: number }): Promise<E4LaneList>
  getE4Lane(laneId: string): Promise<E4LaneDetail>
  listE4Claims(options?: { readonly accepted?: boolean; readonly targetFamily?: string; readonly kind?: string; readonly offset?: number; readonly limit?: number }): Promise<E4ClaimList>
  getE4Claim(claimId: string): Promise<E4ClaimDetail>
  getE4Catalog(options?: { readonly laneId?: string; readonly artifactKind?: string; readonly offset?: number; readonly limit?: number }): Promise<E4CatalogPage>
  getE4CatalogBinding(): Promise<E4CatalogBinding>
  getE4LedgerRows(options?: { readonly featureId?: string; readonly laneId?: string; readonly offset?: number; readonly limit?: number }): Promise<E4LedgerRows>
  listE4Records(schemaVersion: string, options?: { readonly laneId?: string; readonly source?: "evidence" | "runtime"; readonly offset?: number; readonly limit?: number }): Promise<E4RecordList>
  reverifyE4Claim(claimId: string, body?: E4ReverifyRequest): Promise<E4ReverifyResult>
  getE4Coverage(targetFamily: string): Promise<E4CoverageMatrix>
  listRegistries(): Promise<RegistryList>
  getRegistry(registryId: string): Promise<Record<string, unknown>>
  submitRlRun(payload: RlRunSubmitRequest): Promise<RlRunStatus>
  getRlRun(runId: string, tenantId: string, workspaceId: string): Promise<RlRunStatus>
  getRlRunEvents(runId: string, tenantId: string, workspaceId: string, fromSequence?: number): Promise<string>
  cancelRlRun(runId: string, tenantId: string, workspaceId: string, reason?: string): Promise<RlRunStatus>
  listRlArtifacts(runId: string, tenantId: string, workspaceId: string): Promise<{ readonly run_id: string; readonly artifacts: RlArtifact[] }>
  replayRlArtifact(runId: string, artifactId: string, tenantId: string, workspaceId: string): Promise<{ readonly available: boolean; readonly artifact_id: string; readonly replay_path?: string; readonly sha256?: string; readonly reason?: string }>
  getRlAudit(runId: string, tenantId: string, workspaceId: string): Promise<RlAuditReport>
  providerAuthAttach(body: ProviderAuthAttachRequest): Promise<ProviderAuthAttachResponse>
  providerAuthDetach(body: ProviderAuthDetachRequest): Promise<ProviderAuthDetachResponse>
  providerAuthStatus(): Promise<ProviderAuthStatusResponse>
  downloadArtifact(sessionId: string, artifact: string): Promise<string>
  uploadAttachments(sessionId: string, attachments: ReadonlyArray<AttachmentUploadPayload>): Promise<AttachmentHandle[]>
}

const API_AUTH_ABORTED = Symbol("api-auth-aborted")

const abortError = (): DOMException => new DOMException("Aborted", "AbortError")

const resolveApiAuthTokenWithAbort = async (
  config: ApiClientConfig,
  signal: AbortSignal,
): Promise<string | undefined | typeof API_AUTH_ABORTED> => {
  if (signal.aborted) return API_AUTH_ABORTED
  if (typeof config.authToken !== "function") return config.authToken
  const authTokenProvider = config.authToken

  return await new Promise<string | undefined | typeof API_AUTH_ABORTED>((resolve, reject) => {
    const onAbort = () => resolve(API_AUTH_ABORTED)
    signal.addEventListener("abort", onAbort, { once: true })
    Promise.resolve()
      .then(() => authTokenProvider())
      .then(resolve, reject)
      .finally(() => {
        signal.removeEventListener("abort", onAbort)
      })
  })
}

const applyAuthHeader = async (
  headers: Record<string, string>,
  config: ApiClientConfig,
  signal: AbortSignal,
): Promise<void> => {
  const authToken = await resolveApiAuthTokenWithAbort(config, signal)
  if (authToken === API_AUTH_ABORTED) throw abortError()
  if (authToken) headers.Authorization = `Bearer ${authToken}`
}

const buildUrl = (baseUrl: string, path: string, query?: RequestOptions["query"]): URL => {
  const url = new URL(path.replace(/^\/+/, ""), baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue
      url.searchParams.set(key, String(value))
    }
  }
  return url
}

const isE4ApiErrorEnvelope = (value: unknown): value is E4ApiErrorEnvelope => {
  if (value === null || typeof value !== "object") return false
  const candidate = value as { error?: unknown; detail?: unknown; path?: unknown }
  return typeof candidate.error === "string"
}

const apiErrorMessage = (status: number, payload: unknown): string => {
  if (isE4ApiErrorEnvelope(payload)) {
    return payload.detail ? `${payload.error}: ${payload.detail}` : payload.error
  }
  return `Request failed with status ${status}`
}

const requestWithConfig = async <T>(
  config: ApiClientConfig,
  path: string,
  method: JsonMethod,
  options: RequestOptions = {},
): Promise<T> => {
  const url = buildUrl(config.baseUrl, path, options.query)
  const controller = new AbortController()
  const timeoutMs = config.requestTimeoutMs ?? 30_000
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ?? {}),
  }
  try {
    await applyAuthHeader(headers, config, controller.signal)

    const response = await fetch(url, {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    })
    const responseType = options.responseType ?? "json"
    const contentType = response.headers.get("content-type") ?? ""
    const isJson = contentType.includes("application/json")
    if (!response.ok) {
      const payload = isJson ? await response.json().catch(() => undefined) : await response.text().catch(() => undefined)
      throw new ApiError(apiErrorMessage(response.status, payload), response.status, payload)
    }
    if (method === "DELETE") {
      return undefined as T
    }
    if (responseType === "text" || !isJson) {
      return (await response.text()) as unknown as T
    }
    return (await response.json()) as T
  } finally {
    clearTimeout(timeout)
  }
}

const toApiConfig = (config: AppConfig): ApiClientConfig => ({
  baseUrl: config.baseUrl,
  authToken: config.authToken,
  requestTimeoutMs: config.requestTimeoutMs,
})

export const createApiClient = (config: ApiClientConfig): ApiClientInstance => ({
  health: () => requestWithConfig<HealthResponse>(config, "/health", "GET"),
  engineStatus: () => requestWithConfig<EngineStatusResponse>(config, "/v1/status", "GET"),
  createSession: (payload: SessionCreateRequest) =>
    requestWithConfig<SessionCreateResponse>(config, "/v1/sessions", "POST", { body: payload }),
  listSessions: () => requestWithConfig<SessionSummary[]>(config, "/v1/sessions", "GET"),
  getSession: (sessionId: string) => requestWithConfig<SessionSummary>(config, `/v1/sessions/${sessionId}`, "GET"),
  listSessionRecords: (sessionId: string, options?: { readonly schemaVersion?: string; readonly offset?: number; readonly limit?: number }) =>
    requestWithConfig<SessionKernelRecordList>(config, `/v1/sessions/${sessionId}/records`, "GET", {
      query: {
        schema_version: options?.schemaVersion,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  postInput: (sessionId: string, body: { content: string; attachments?: ReadonlyArray<string> }) =>
    requestWithConfig<SessionInputResponse>(config, `/v1/sessions/${sessionId}/input`, "POST", { body }),
  postCommand: (sessionId: string, body: Record<string, unknown>) =>
    requestWithConfig<SessionCommandResponse>(config, `/v1/sessions/${sessionId}/command`, "POST", { body }),
  deleteSession: (sessionId: string) => requestWithConfig<void>(config, `/v1/sessions/${sessionId}`, "DELETE"),
  listSessionFiles: (sessionId: string, path?: string) =>
    requestWithConfig<SessionFileInfo[]>(config, `/v1/sessions/${sessionId}/files`, "GET", { query: path ? { path } : undefined }),
  readSessionFile: (sessionId: string, filePath: string, options?: ReadSessionFileOptions) =>
    requestWithConfig<SessionFileContent>(config, `/v1/sessions/${sessionId}/files/content`, "GET", {
      query: {
        path: filePath,
        mode: options?.mode ?? "cat",
        head_lines: options?.headLines,
        tail_lines: options?.tailLines,
        max_bytes: options?.maxBytes,
      },
    }),
  getModelCatalog: (configPath: string) =>
    requestWithConfig<ModelCatalogResponse>(config, "/v1/models", "GET", { query: { config_path: configPath } }),
  getSkillsCatalog: (sessionId: string) =>
    requestWithConfig<SkillCatalogResponse>(config, `/v1/sessions/${sessionId}/skills`, "GET"),
  getCtreeSnapshot: (sessionId: string) =>
    requestWithConfig<CTreeSnapshotResponse>(config, `/v1/sessions/${sessionId}/ctrees`, "GET"),
  getCtreeTree: (
    sessionId: string,
    options?: {
      readonly stage?: string
      readonly includePreviews?: boolean
      readonly source?: string
    },
  ) =>
    requestWithConfig<CTreeTreeResponse>(config, `/sessions/${sessionId}/ctrees/tree`, "GET", {
      query: {
        stage: options?.stage,
        include_previews: options?.includePreviews,
        source: options?.source,
      },
    }),
  getCtreeDisk: (sessionId: string) =>
    requestWithConfig<CTreeDiskArtifactsResponse>(config, `/sessions/${sessionId}/ctrees/disk`, "GET"),
  getCtreeEvents: (
    sessionId: string,
    options?: {
      readonly source?: string
      readonly offset?: number
      readonly limit?: number
    },
  ) =>
    requestWithConfig<CTreeEventsResponse>(config, `/sessions/${sessionId}/ctrees/events`, "GET", {
      query: {
        source: options?.source,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  getE4Health: () => requestWithConfig<E4Health>(config, "/v1/e4/health", "GET"),
  listE4Schemas: () => requestWithConfig<E4SchemaList>(config, "/v1/e4/schemas", "GET"),
  listE4Lanes: (options?: { readonly phase?: string; readonly kind?: string; readonly targetFamily?: string; readonly status?: string; readonly offset?: number; readonly limit?: number }) =>
    requestWithConfig<E4LaneList>(config, "/v1/e4/lanes", "GET", {
      query: {
        phase: options?.phase,
        kind: options?.kind,
        target_family: options?.targetFamily,
        status: options?.status,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  getE4Lane: (laneId: string) => requestWithConfig<E4LaneDetail>(config, `/v1/e4/lanes/${laneId}`, "GET"),
  listE4Claims: (options?: { readonly accepted?: boolean; readonly targetFamily?: string; readonly kind?: string; readonly offset?: number; readonly limit?: number }) =>
    requestWithConfig<E4ClaimList>(config, "/v1/e4/claims", "GET", {
      query: {
        accepted: options?.accepted,
        target_family: options?.targetFamily,
        kind: options?.kind,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  getE4Claim: (claimId: string) => requestWithConfig<E4ClaimDetail>(config, `/v1/e4/claims/${claimId}`, "GET"),
  getE4Catalog: (options?: { readonly laneId?: string; readonly artifactKind?: string; readonly offset?: number; readonly limit?: number }) =>
    requestWithConfig<E4CatalogPage>(config, "/v1/e4/catalog", "GET", {
      query: {
        lane_id: options?.laneId,
        artifact_kind: options?.artifactKind,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  getE4CatalogBinding: () =>
    requestWithConfig<E4CatalogBinding>(config, "/v1/e4/catalog/binding", "GET"),
  getE4LedgerRows: (options?: { readonly featureId?: string; readonly laneId?: string; readonly offset?: number; readonly limit?: number }) =>
    requestWithConfig<E4LedgerRows>(config, "/v1/e4/ledger/rows", "GET", {
      query: {
        feature_id: options?.featureId,
        lane_id: options?.laneId,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  listE4Records: (schemaVersion: string, options?: { readonly laneId?: string; readonly source?: "evidence" | "runtime"; readonly offset?: number; readonly limit?: number }) =>
    requestWithConfig<E4RecordList>(config, "/v1/e4/records", "GET", {
      query: {
        schema_version: schemaVersion,
        lane_id: options?.laneId,
        source: options?.source,
        offset: options?.offset,
        limit: options?.limit,
      },
    }),
  reverifyE4Claim: (claimId: string, body: E4ReverifyRequest = {}) =>
    requestWithConfig<E4ReverifyResult>(config, `/v1/e4/claims/${claimId}/reverify`, "POST", { body }),
  getE4Coverage: (targetFamily: string) =>
    requestWithConfig<E4CoverageMatrix>(config, `/v1/e4/coverage/${targetFamily}`, "GET"),
  listRegistries: () => requestWithConfig<RegistryList>(config, "/v1/registries", "GET"),
  getRegistry: (registryId: string) =>
    requestWithConfig<Record<string, unknown>>(config, `/v1/registries/${registryId}`, "GET"),
  submitRlRun: (payload: RlRunSubmitRequest) =>
    requestWithConfig<RlRunStatus>(config, "/v1/rl/runs", "POST", { body: payload }),
  getRlRun: (runId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<RlRunStatus>(config, `/v1/rl/runs/${runId}`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  getRlRunEvents: (runId: string, tenantId: string, workspaceId: string, fromSequence = 0) =>
    requestWithConfig<string>(config, `/v1/rl/runs/${runId}/events`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId, from_sequence: fromSequence }, responseType: "text" }),
  cancelRlRun: (runId: string, tenantId: string, workspaceId: string, reason?: string) =>
    requestWithConfig<RlRunStatus>(config, `/v1/rl/runs/${runId}/cancel`, "POST", { body: { tenant_id: tenantId, workspace_id: workspaceId, reason } }),
  listRlArtifacts: (runId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<{ readonly run_id: string; readonly artifacts: RlArtifact[] }>(config, `/v1/rl/runs/${runId}/artifacts`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  replayRlArtifact: (runId: string, artifactId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<{ readonly available: boolean; readonly artifact_id: string; readonly replay_path?: string; readonly sha256?: string; readonly reason?: string }>(config, `/v1/rl/runs/${runId}/replay/${artifactId}`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  getRlAudit: (runId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<RlAuditReport>(config, `/v1/rl/runs/${runId}/audit`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  providerAuthAttach: (body: ProviderAuthAttachRequest) =>
    requestWithConfig<ProviderAuthAttachResponse>(config, "/v1/provider-auth/attach", "POST", { body }),
  providerAuthDetach: (body: ProviderAuthDetachRequest) =>
    requestWithConfig<ProviderAuthDetachResponse>(config, "/v1/provider-auth/detach", "POST", { body }),
  providerAuthStatus: () =>
    requestWithConfig<ProviderAuthStatusResponse>(config, "/v1/provider-auth/status", "GET"),
  downloadArtifact: (sessionId: string, artifact: string) =>
    requestWithConfig<string>(config, `/v1/sessions/${sessionId}/download`, "GET", { query: { artifact }, responseType: "text" }),
  uploadAttachments: async (sessionId: string, attachments: ReadonlyArray<AttachmentUploadPayload>) => {
    if (attachments.length === 0) return []
    const url = buildUrl(config.baseUrl, `/v1/sessions/${sessionId}/attachments`)
    const form = new FormData()
    attachments.forEach((attachment, index) => {
      const buffer = Buffer.from(attachment.base64, "base64")
      const blob = new Blob([buffer], { type: attachment.mime || "application/octet-stream" })
      const filename = attachment.filename ?? `attachment-${index + 1}.bin`
      form.append("files", blob, filename)
    })
    form.append("metadata", JSON.stringify({ source: "clipboard" }))
    const headers: Record<string, string> = {}
    const controller = new AbortController()
    const timeoutMs = config.requestTimeoutMs ?? 30_000
    const timeout = setTimeout(() => controller.abort(), timeoutMs)
    try {
      await applyAuthHeader(headers, config, controller.signal)
      const response = await fetch(url, {
        method: "POST",
        headers,
        body: form,
        signal: controller.signal,
      })
      const contentType = response.headers.get("content-type") ?? ""
      const isJson = contentType.includes("application/json")
      if (!response.ok) {
        const payload = isJson ? await response.json().catch(() => undefined) : await response.text().catch(() => undefined)
        throw new ApiError(`Attachment upload failed with status ${response.status}`, response.status, payload)
      }
      if (!isJson) {
        return []
      }
      const payload = (await response.json()) as { attachments?: AttachmentHandle[] }
      return payload.attachments ?? []
    } finally {
      clearTimeout(timeout)
    }
  },
})


const buildApiClient = (): ApiClientInstance => {
  const config = loadAppConfig()
  return createApiClient({
    ...toApiConfig(config),
    authToken: () => resolveAuthToken(config.baseUrl),
  })
}

export const ApiClient: ApiClientInstance = {
  health: (...args) => buildApiClient().health(...args),
  engineStatus: (...args) => buildApiClient().engineStatus(...args),
  createSession: (...args) => buildApiClient().createSession(...args),
  listSessions: (...args) => buildApiClient().listSessions(...args),
  getSession: (...args) => buildApiClient().getSession(...args),
  listSessionRecords: (...args) => buildApiClient().listSessionRecords(...args),
  postInput: (...args) => buildApiClient().postInput(...args),
  postCommand: (...args) => buildApiClient().postCommand(...args),
  deleteSession: (...args) => buildApiClient().deleteSession(...args),
  listSessionFiles: (...args) => buildApiClient().listSessionFiles(...args),
  readSessionFile: (...args) => buildApiClient().readSessionFile(...args),
  getModelCatalog: (...args) => buildApiClient().getModelCatalog(...args),
  getSkillsCatalog: (...args) => buildApiClient().getSkillsCatalog(...args),
  getCtreeSnapshot: (...args) => buildApiClient().getCtreeSnapshot(...args),
  getCtreeTree: (...args) => buildApiClient().getCtreeTree(...args),
  getCtreeDisk: (...args) => buildApiClient().getCtreeDisk(...args),
  getCtreeEvents: (...args) => buildApiClient().getCtreeEvents(...args),
  getE4Health: (...args) => buildApiClient().getE4Health(...args),
  listE4Schemas: (...args) => buildApiClient().listE4Schemas(...args),
  listE4Lanes: (...args) => buildApiClient().listE4Lanes(...args),
  getE4Lane: (...args) => buildApiClient().getE4Lane(...args),
  listE4Claims: (...args) => buildApiClient().listE4Claims(...args),
  getE4Claim: (...args) => buildApiClient().getE4Claim(...args),
  getE4Catalog: (...args) => buildApiClient().getE4Catalog(...args),
  getE4CatalogBinding: (...args) => buildApiClient().getE4CatalogBinding(...args),
  getE4LedgerRows: (...args) => buildApiClient().getE4LedgerRows(...args),
  listE4Records: (...args) => buildApiClient().listE4Records(...args),
  reverifyE4Claim: (...args) => buildApiClient().reverifyE4Claim(...args),
  getE4Coverage: (...args) => buildApiClient().getE4Coverage(...args),
  listRegistries: (...args) => buildApiClient().listRegistries(...args),
  getRegistry: (...args) => buildApiClient().getRegistry(...args),
  submitRlRun: (...args) => buildApiClient().submitRlRun(...args),
  getRlRun: (...args) => buildApiClient().getRlRun(...args),
  getRlRunEvents: (...args) => buildApiClient().getRlRunEvents(...args),
  cancelRlRun: (...args) => buildApiClient().cancelRlRun(...args),
  listRlArtifacts: (...args) => buildApiClient().listRlArtifacts(...args),
  replayRlArtifact: (...args) => buildApiClient().replayRlArtifact(...args),
  getRlAudit: (...args) => buildApiClient().getRlAudit(...args),
  providerAuthAttach: (...args) => buildApiClient().providerAuthAttach(...args),
  providerAuthDetach: (...args) => buildApiClient().providerAuthDetach(...args),
  providerAuthStatus: (...args) => buildApiClient().providerAuthStatus(...args),
  downloadArtifact: (...args) => buildApiClient().downloadArtifact(...args),
  uploadAttachments: (...args) => buildApiClient().uploadAttachments(...args),
}

export type {
  SessionCreateRequest,
  SessionCreateResponse,
  SessionEvent,
  SessionSummary,
  SessionKernelRecordList,
  ErrorResponse,
  SessionFileInfo,
  SessionFileContent,
  SessionInputResponse,
  SessionCommandResponse,
  HealthResponse,
  EngineStatusResponse,
  ModelCatalogResponse,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
  CTreeTreeResponse,
  CTreeDiskArtifactsResponse,
  CTreeEventsResponse,
  ProviderAuthAttachRequest,
  ProviderAuthAttachResponse,
  ProviderAuthDetachRequest,
  ProviderAuthDetachResponse,
  ProviderAuthStatusResponse,
  RlRunSubmitRequest,
  RlRunStatus,
  RlStreamEvent,
  RlArtifact,
  RlAuditReport,
}
