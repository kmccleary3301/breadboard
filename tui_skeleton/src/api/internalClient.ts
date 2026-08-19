import fs from "node:fs"
import { homedir } from "node:os"
import path from "node:path"
import { loadAppConfig, type AppConfig, DEFAULT_CONFIG_PATH } from "../config/appConfig.js"
import { resolveAuthToken } from "../config/authTokenProvider.js"
import { ApiError } from "./client.js"
export { ApiError } from "./client.js"
import type {
  AttachmentHandle,
  AttachmentUploadResponse,
  Body_upload_attachments_v1_sessions__session_id__attachments_post,
  ErrorResponse,
  SessionCreateRequest,
  SessionCreateResponse,
  SessionSummary,
  SessionFileInfo,
  SessionFileContent,
  SessionInputRequest,
  SessionInputResponse,
  SessionCommandRequest,
  SessionCommandResponse,
  ModelCatalogResponse,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
  ProviderAuthAttachRequest,
  ProviderAuthAttachResponse,
  ProviderAuthDetachRequest,
  ProviderAuthDetachResponse,
  ProviderAuthStatusResponse,
  RLRunArtifactListResponse,
  RLRunAuditResponse,
  RLRunCancelRequest,
  RLRunReplayResponse,
  RLRunStatusResponse,
  RLRunSubmitRequest,
  RLRunSubmitResponse,
  E4CatalogPage,
  E4CatalogBinding,
  E4ClaimDetail,
  E4ClaimList,
  E4CoverageMatrix,
  E4Health,
  E4LaneDetail,
  E4LaneList,
  E4LedgerRows,
  E4RecordList,
  E4ReverifyRequest,
  E4ReverifyResult,
  E4SchemaList,
  RegistryList,
} from "./generated/internal-openapi-types.js"
import type {
  SessionEvent,
  SessionKernelRecordList,
  HealthResponse,
  EngineStatusResponse,
  CTreeTreeResponse,
  CTreeDiskArtifactsResponse,
  CTreeEventsResponse,
  E4ApiErrorEnvelope,
} from "./types.js"


type JsonMethod = "GET" | "POST" | "PUT" | "DELETE"

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
export type PublicActionId =
  | "public.system.describe" | "public.system.health" | "public.system.schemas"
  | "public.harness.create" | "public.harness.list" | "public.harness.get" | "public.harness.update"
  | "public.harness.validate" | "public.harness.explain" | "public.harness.lock" | "public.harness_lock.get"
  | "public.integration.list" | "public.integration.get" | "public.integration.probe"
  | "public.artifact.list" | "public.artifact.get" | "public.artifact.verify"
  | "public.session.start" | "public.session.list" | "public.session.get" | "public.session.send_input"
  | "public.session.approve" | "public.session.resume" | "public.session.cancel"
  | "public.session.events" | "public.session.artifacts"

export interface ApiClientInstance {
  invokePublicAction(actionId: PublicActionId, input?: Readonly<Record<string, unknown>>): Promise<unknown>
  health(): Promise<HealthResponse>
  engineStatus(): Promise<EngineStatusResponse>
  createSession(payload: SessionCreateRequest): Promise<SessionCreateResponse>
  listSessions(): Promise<SessionSummary[]>
  getSession(sessionId: string): Promise<SessionSummary>
  listSessionRecords(sessionId: string, options?: { readonly schemaVersion?: string; readonly offset?: number; readonly limit?: number }): Promise<SessionKernelRecordList>
  postInput(sessionId: string, body: SessionInputRequest): Promise<SessionInputResponse>
  postCommand(sessionId: string, body: SessionCommandRequest): Promise<SessionCommandResponse>
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
  submitRlRun(payload: RLRunSubmitRequest): Promise<RLRunSubmitResponse>
  getRlRun(runId: string, tenantId: string, workspaceId: string): Promise<RLRunStatusResponse>
  getRlRunEvents(runId: string, tenantId: string, workspaceId: string, fromSequence?: number): Promise<string>
  cancelRlRun(runId: string, tenantId: string, workspaceId: string, reason?: string): Promise<RLRunStatusResponse>
  listRlArtifacts(runId: string, tenantId: string, workspaceId: string): Promise<RLRunArtifactListResponse>
  replayRlArtifact(runId: string, artifactId: string, tenantId: string, workspaceId: string): Promise<RLRunReplayResponse>
  getRlAudit(runId: string, tenantId: string, workspaceId: string): Promise<RLRunAuditResponse>
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

const CODEX_AUTH_PATH = path.join(homedir(), ".codex", "auth.json")
const OPENAI_PROVIDER_ID = "openai"
const OPENROUTER_PROVIDER_ID = "openrouter"

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
const publicIdentifier = (input: Readonly<Record<string, unknown>>, name: string): string => {
  const value = input[name]
  if (typeof value !== "string" || value.length === 0) throw new Error(`${name} is required`)
  return encodeURIComponent(value)
}
const publicResource = (input: Readonly<Record<string, unknown>>, name: string): string => {
  const value = input[name]
  if (typeof value !== "string" || value.length === 0) throw new Error(`${name} is required`)
  const parts = value.split("/")
  if (parts.some((part) => part === "" || part === "." || part === "..")) {
    throw new Error(`${name} cannot contain empty or dot segments`)
  }
  return parts.map(encodeURIComponent).join("/")
}
const publicHeaders = (input: Readonly<Record<string, unknown>>): Record<string, string> | undefined =>
  typeof input.idempotency_key === "string" && input.idempotency_key.length > 0
    ? { "Idempotency-Key": input.idempotency_key }
    : undefined
const requireLegacyRoutes = (): void => {
  const enabled = new Set(["1", "true", "yes", "on"]).has(
    (process.env.BREADBOARD_LEGACY_ROUTES ?? "").trim().toLowerCase(),
  )
  if (!enabled) {
    throw new ApiError(
      "This internal TUI operation requires BREADBOARD_LEGACY_ROUTES=1",
      404,
    )
  }
}
const invokePublicAction = (
  config: ApiClientConfig,
  actionId: PublicActionId,
  input: Readonly<Record<string, unknown>> = {},
): Promise<unknown> => {
  const request = (method: JsonMethod, route: string, options: RequestOptions = {}) =>
    requestWithConfig<unknown>(config, route, method, options)
  switch (actionId) {
    case "public.system.describe": return request("GET", "/v1/system")
    case "public.system.health": return request("GET", "/v1/health")
    case "public.system.schemas": return request("GET", "/v1/schemas")
    case "public.harness.create": return request("POST", "/v1/harnesses", { body: { directory: input.directory ?? "." } })
    case "public.harness.list": return request("GET", "/v1/harnesses")
    case "public.harness.get": return request("GET", `/v1/harnesses/${publicResource(input, "harness_id")}`)
    case "public.harness.update": return request("PUT", `/v1/harnesses/${publicResource(input, "harness_id")}`, { body: { definition: input.definition } })
    case "public.harness.validate": return request("POST", `/v1/harnesses/${publicResource(input, "harness_id")}/validate`)
    case "public.harness.explain": return request("POST", `/v1/harnesses/${publicResource(input, "harness_id")}/explain`)
    case "public.harness.lock": return request("POST", `/v1/harnesses/${publicResource(input, "harness_id")}/lock`)
    case "public.harness_lock.get": return request("GET", `/v1/harness-locks/${publicResource(input, "lock_id")}`)
    case "public.integration.list": return request("GET", "/v1/integrations")
    case "public.integration.get": return request("GET", `/v1/integrations/${publicIdentifier(input, "integration_id")}`)
    case "public.integration.probe": return request("POST", `/v1/integrations/${publicIdentifier(input, "integration_id")}/probe`, { headers: publicHeaders(input) })
    case "public.artifact.list": return request("GET", "/v1/artifacts")
    case "public.artifact.get": return request("GET", `/v1/artifacts/${publicIdentifier(input, "artifact_id")}`)
    case "public.artifact.verify": return request("POST", `/v1/artifacts/${publicIdentifier(input, "artifact_id")}/verify`)
    case "public.session.start": {
      const body = { ...input }
      delete body.idempotency_key
      return request("POST", "/v1/sessions", { body, headers: publicHeaders(input) })
    }
    case "public.session.list": return request("GET", "/v1/sessions")
    case "public.session.get": return request("GET", `/v1/sessions/${publicIdentifier(input, "session_id")}`)
    case "public.session.send_input": return request("POST", `/v1/sessions/${publicIdentifier(input, "session_id")}/input`, { body: { content: input.content }, headers: publicHeaders(input) })
    case "public.session.approve": return request("POST", `/v1/sessions/${publicIdentifier(input, "session_id")}/approve`, { body: { request_id: input.request_id, decision: input.decision }, headers: publicHeaders(input) })
    case "public.session.resume": return request("POST", `/v1/sessions/${publicIdentifier(input, "session_id")}/resume`, { headers: publicHeaders(input) })
    case "public.session.cancel": return request("POST", `/v1/sessions/${publicIdentifier(input, "session_id")}/cancel`, { body: { reason: input.reason ?? "operator request" }, headers: publicHeaders(input) })
    case "public.session.events": {
      const sessionId = publicIdentifier(input, "session_id")
      const query: Record<string, number> = {}
      if (typeof input.resume_token === "number") query.resume_token = input.resume_token
      if (typeof input.limit === "number") query.limit = input.limit
      const lastEventId = typeof input.last_event_id === "number" ? String(input.last_event_id) : undefined
      return import("./stream.js").then(({ streamSessionEvents }) =>
        streamSessionEvents(decodeURIComponent(sessionId), { config, query, lastEventId }))
    }
    case "public.session.artifacts": return request("GET", `/v1/sessions/${publicIdentifier(input, "session_id")}/artifacts`)
  }
}

const toApiConfig = (config: AppConfig): ApiClientConfig => ({
  baseUrl: config.baseUrl,
  authToken: config.authToken,
  requestTimeoutMs: config.requestTimeoutMs,
})

const isLocalBaseUrl = (value: string): boolean => {
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    return host === "localhost" || host === "127.0.0.1" || host === "::1"
  } catch {
    return false
  }
}

const findCodexToken = (value: unknown, seen = new Set<unknown>()): string | null => {
  if (!value || typeof value !== "object") return null
  if (seen.has(value)) return null
  seen.add(value)
  if (Array.isArray(value)) {
    for (const item of value) {
      const nested = findCodexToken(item, seen)
      if (nested) return nested
    }
    return null
  }
  const record = value as Record<string, unknown>
  for (const key of ["codex_access_token", "access_token", "id_token", "token", "auth_token"]) {
    const candidate = record[key]
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim()
    }
  }
  for (const nested of Object.values(record)) {
    const candidate = findCodexToken(nested, seen)
    if (candidate) return candidate
  }
  return null
}

const readLocalProviderAuthMaterial = (
  providerId: typeof OPENAI_PROVIDER_ID | typeof OPENROUTER_PROVIDER_ID,
): { token: string; isSubscriptionPlan: boolean } | null => {
  const envKey =
    providerId === OPENROUTER_PROVIDER_ID
      ? process.env.OPENROUTER_API_KEY?.trim()
      : process.env.OPENAI_API_KEY?.trim()
  if (envKey) {
    return { token: envKey, isSubscriptionPlan: false }
  }
  if (providerId === OPENROUTER_PROVIDER_ID) {
    return null
  }
  try {
    if (!fs.existsSync(CODEX_AUTH_PATH)) return null
    const parsed = JSON.parse(fs.readFileSync(CODEX_AUTH_PATH, "utf8")) as unknown
    if (!parsed || typeof parsed !== "object") return null
    const record = parsed as Record<string, unknown>
    const authFileApiKey = typeof record.OPENAI_API_KEY === "string" ? record.OPENAI_API_KEY.trim() : ""
    if (authFileApiKey) {
      return { token: authFileApiKey, isSubscriptionPlan: false }
    }
    const codexToken = findCodexToken(parsed)
    if (codexToken) {
      return { token: codexToken, isSubscriptionPlan: true }
    }
  } catch {
    return null
  }
  return null
}

const ensureLocalProviderAuth = async (configPath?: string): Promise<void> => {
  const appConfig = loadAppConfig()
  if (!isLocalBaseUrl(appConfig.baseUrl)) return
  const apiConfig: ApiClientConfig = {
    ...toApiConfig(appConfig),
    authToken: () => resolveAuthToken(appConfig.baseUrl),
  }
  let status: ProviderAuthStatusResponse | null = null
  try {
    status = await requestWithConfig<ProviderAuthStatusResponse>(apiConfig, "/v1/provider-auth/status", "GET")
  } catch {
    return
  }
  const attached = status?.attached ?? []
  const attachIfMissing = async (
    providerId: typeof OPENAI_PROVIDER_ID | typeof OPENROUTER_PROVIDER_ID,
  ): Promise<void> => {
    if (attached.some((row) => row.provider_id === providerId)) {
      return
    }
    const material = readLocalProviderAuthMaterial(providerId)
    if (!material) return
    try {
      await requestWithConfig<ProviderAuthAttachResponse>(apiConfig, "/v1/provider-auth/attach", "POST", {
        body: {
          material: {
            provider_id: providerId,
            api_key: material.token,
            headers: { Authorization: `Bearer ${material.token}` },
            is_subscription_plan: providerId === OPENAI_PROVIDER_ID ? material.isSubscriptionPlan : false,
          },
          config_path: configPath ?? DEFAULT_CONFIG_PATH,
        },
      })
    } catch {
      // Best-effort bootstrap only. Surface the real provider error later if attach fails.
    }
  }

  await attachIfMissing(OPENROUTER_PROVIDER_ID)
  await attachIfMissing(OPENAI_PROVIDER_ID)
}

export const createApiClient = (config: ApiClientConfig): ApiClientInstance => ({
  invokePublicAction: (actionId, input) => invokePublicAction(config, actionId, input),
  health: () => requestWithConfig<HealthResponse>(config, "/v1/health", "GET"),
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
  postInput: (sessionId: string, body: SessionInputRequest) =>
    requestWithConfig<SessionInputResponse>(config, `/v1/sessions/${sessionId}/input`, "POST", { body }),
  postCommand: (sessionId: string, body: SessionCommandRequest) =>
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
  ) => {
    requireLegacyRoutes()
    return requestWithConfig<CTreeTreeResponse>(config, `/sessions/${sessionId}/ctrees/tree`, "GET", {
      query: {
        stage: options?.stage,
        include_previews: options?.includePreviews,
        source: options?.source,
      },
    })
  },
  getCtreeDisk: (sessionId: string) => {
    requireLegacyRoutes()
    return requestWithConfig<CTreeDiskArtifactsResponse>(config, `/sessions/${sessionId}/ctrees/disk`, "GET")
  },
  getCtreeEvents: (
    sessionId: string,
    options?: {
      readonly source?: string
      readonly offset?: number
      readonly limit?: number
    },
  ) => {
    requireLegacyRoutes()
    return requestWithConfig<CTreeEventsResponse>(config, `/sessions/${sessionId}/ctrees/events`, "GET", {
      query: {
        source: options?.source,
        offset: options?.offset,
        limit: options?.limit,
      },
    })
  },
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
  submitRlRun: (payload: RLRunSubmitRequest) =>
    requestWithConfig<RLRunSubmitResponse>(config, "/v1/rl/runs", "POST", { body: payload }),
  getRlRun: (runId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<RLRunStatusResponse>(config, `/v1/rl/runs/${runId}`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  getRlRunEvents: (runId: string, tenantId: string, workspaceId: string, fromSequence = 0) =>
    requestWithConfig<string>(config, `/v1/rl/runs/${runId}/events`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId, from_sequence: fromSequence }, responseType: "text" }),
  cancelRlRun: (runId: string, tenantId: string, workspaceId: string, reason?: string) =>
    requestWithConfig<RLRunStatusResponse>(config, `/v1/rl/runs/${runId}/cancel`, "POST", { body: { tenant_id: tenantId, workspace_id: workspaceId, reason } satisfies RLRunCancelRequest }),
  listRlArtifacts: (runId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<RLRunArtifactListResponse>(config, `/v1/rl/runs/${runId}/artifacts`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  replayRlArtifact: (runId: string, artifactId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<RLRunReplayResponse>(config, `/v1/rl/runs/${runId}/replay/${artifactId}`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
  getRlAudit: (runId: string, tenantId: string, workspaceId: string) =>
    requestWithConfig<RLRunAuditResponse>(config, `/v1/rl/runs/${runId}/audit`, "GET", { query: { tenant_id: tenantId, workspace_id: workspaceId } }),
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
    const uploadFields = {
      files: "files",
      metadata: "metadata",
    } as const satisfies { readonly [Field in keyof Body_upload_attachments_v1_sessions__session_id__attachments_post]-?: Field }
    const form = new FormData()
    attachments.forEach((attachment, index) => {
      const buffer = Buffer.from(attachment.base64, "base64")
      const blob = new Blob([buffer], { type: attachment.mime || "application/octet-stream" })
      const filename = attachment.filename ?? `attachment-${index + 1}.bin`
      form.append(uploadFields.files, blob, filename)
    })
    form.append(uploadFields.metadata, JSON.stringify({ source: "clipboard" }))
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
      const payload = (await response.json()) as AttachmentUploadResponse
      return [...payload.attachments]
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
  invokePublicAction: (...args) => buildApiClient().invokePublicAction(...args),
  health: (...args) => buildApiClient().health(...args),
  engineStatus: (...args) => buildApiClient().engineStatus(...args),
  createSession: async (...args) => {
    await ensureLocalProviderAuth(args[0]?.config_path)
    return buildApiClient().createSession(...args)
  },
  listSessions: (...args) => buildApiClient().listSessions(...args),
  getSession: (...args) => buildApiClient().getSession(...args),
  listSessionRecords: (...args) => buildApiClient().listSessionRecords(...args),
  postInput: (...args) => buildApiClient().postInput(...args),
  postCommand: (...args) => buildApiClient().postCommand(...args),
  deleteSession: (...args) => buildApiClient().deleteSession(...args),
  listSessionFiles: (...args) => buildApiClient().listSessionFiles(...args),
  readSessionFile: (...args) => buildApiClient().readSessionFile(...args),
  getModelCatalog: async (...args) => {
    await ensureLocalProviderAuth(args[0])
    return buildApiClient().getModelCatalog(...args)
  },
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
  SessionInputRequest,
  SessionInputResponse,
  SessionCommandRequest,
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
  RLRunArtifactListResponse,
  RLRunAuditResponse,
  RLRunCancelRequest,
  RLRunReplayResponse,
  RLRunStatusResponse,
  RLRunSubmitRequest,
  RLRunSubmitResponse,
}
