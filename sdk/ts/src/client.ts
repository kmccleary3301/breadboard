import { streamSessionEvents, type EventStreamOptions } from "./stream.js"
import type {
  AttachmentHandle, AttachmentUploadPayload, CTreeDiskArtifactsResponse, CTreeEventsResponse, CTreeSnapshotResponse,
  CTreeTreeResponse, E4CatalogBinding, E4CatalogPage, E4ClaimDetail, E4ClaimList, E4CoverageMatrix,
  E4Health, E4LaneDetail, E4LaneList, E4LedgerRows, E4RecordList, E4ReverifyRequest, E4ReverifyResult, E4SchemaList,
  EngineStatusResponse, HealthResponse, ModelCatalogResponse, ProviderAuthAttachRequest, ProviderAuthAttachResponse,
  ProviderAuthDetachRequest, ProviderAuthDetachResponse, ProviderAuthStatusResponse, AuthProviderView, AuthCredentialView,
  AuthLoginSession, BeginAuthLogin, CompleteAuthLogin, PutApiKeyInput, AuthActionResponse, ModelRolesResolveInput,
  ModelRolesResolveResponse, ReadSessionFileOptions, RegistryList, RLRunArtifactListResponse, RLRunAuditResponse,
  RLRunCancelRequest, RLRunReplayResponse, RLRunStatusResponse, RLRunSubmitRequest, RLRunSubmitResponse,
  SessionCommandRequest, SessionCommandResponse,
  SessionCreateRequest, SessionCreateResponse, SessionEvent, SessionFileContent, SessionFileInfo, SessionInputRequest,
  SessionInputResponse, SessionKernelRecordList, SessionSummary, SkillCatalogResponse, PublicResult,
} from "./types.js"
import type { Problem } from "./generated/dtos.js"


export class ApiError extends Error {
  readonly status: number
  readonly body?: unknown
  constructor(message: string, status: number, body?: unknown) { super(message); this.name = "ApiError"; this.status = status; this.body = body }
}

type JsonMethod = "GET" | "POST" | "PUT" | "DELETE"
export interface BreadboardClientConfig {
  readonly baseUrl: string
  readonly fetch?: typeof fetch
  readonly authToken?: string | (() => Promise<string | undefined>)
  readonly requestTimeoutMs?: number
  readonly streamSchema?: number | null
  readonly streamIncludeLegacy?: boolean | null
}
export type PublicActionId =
  | "public.system.describe" | "public.system.health" | "public.system.schemas"
  | "public.harness.create" | "public.harness.list" | "public.harness.get" | "public.harness.update"
  | "public.harness.validate" | "public.harness.explain" | "public.harness.lock" | "public.harness_lock.get"
  | "public.integration.list" | "public.integration.get" | "public.integration.probe"
  | "public.artifact.list" | "public.artifact.get" | "public.artifact.verify"
  | "public.session.start" | "public.session.list" | "public.session.get" | "public.session.send_input"
  | "public.session.approve" | "public.session.resume" | "public.session.cancel" | "public.session.events" | "public.session.artifacts"

const valueToken = async (config: BreadboardClientConfig): Promise<string | undefined> => typeof config.authToken === "function" ? config.authToken() : config.authToken
const buildUrl = (baseUrl: string, route: string, query?: Record<string, string | number | boolean | undefined>): URL => {
  const url = new URL(route.replace(/^\/+/, ""), baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
  for (const [key, value] of Object.entries(query ?? {})) if (value !== undefined && value !== null) url.searchParams.set(key, String(value))
  return url
}
const identifier = (value: string, label: string): string => {
  if (!value) throw new Error(`${label} is required`)
  return encodeURIComponent(value)
}
const resource = (value: string, label: string): string => {
  if (!value) throw new Error(`${label} is required`)
  const parts = value.split("/")
  if (parts.some((part) => !part || part === "." || part === "..")) throw new Error(`${label} cannot contain empty or dot segments`)
  return parts.map(encodeURIComponent).join("/")
}
const isProblem = (value: unknown): value is Problem =>
  typeof value === "object"
  && value !== null
  && !Array.isArray(value)
  && "error_code" in value
  && typeof value.error_code === "string"
  && "message" in value
  && typeof value.message === "string"

const detailMessage = (value: unknown): string | undefined => {
  if (typeof value === "string") return value
  if (isProblem(value)) return `${value.error_code}: ${value.message}`
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined
  try {
    const serialized = JSON.stringify(value)
    return serialized === "{}" ? undefined : serialized
  } catch {
    return undefined
  }
}

const apiErrorMessage = (status: number, payload: unknown): string => {
  if (typeof payload === "object" && payload !== null && !Array.isArray(payload)) {
    const error = "error" in payload && typeof payload.error === "string" ? payload.error : undefined
    const detail = "detail" in payload ? detailMessage(payload.detail) : undefined
    if (error) return detail && !detail.startsWith(`${error}:`) ? `${error}: ${detail}` : detail ?? error
    if (detail) return detail
    const problem = detailMessage(payload)
    if (problem) return problem
  }
  return `Request failed with status ${status}`
}
const request = async <T>(config: BreadboardClientConfig, route: string, method: JsonMethod, options: { body?: unknown; query?: Record<string, string | number | boolean | undefined>; responseType?: "json" | "text"; headers?: Record<string, string> } = {}): Promise<T> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs ?? 30_000)
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(options.headers ?? {}) }
  try {
    const token = await valueToken(config)
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await (config.fetch ?? globalThis.fetch)(buildUrl(config.baseUrl, route, options.query), { method, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: controller.signal })
    const contentType = response.headers.get("content-type") ?? ""
    const isJson = contentType.includes("application/json")
    if (!response.ok) {
      const payload = isJson ? await response.json().catch(() => undefined) : await response.text().catch(() => undefined)
      throw new ApiError(apiErrorMessage(response.status, payload), response.status, payload)
    }
    if (options.responseType === "text") return await response.text() as T
    if (isJson) return await response.json() as T
    const text = await response.text()
    return (text === "" ? undefined : text) as T
  } finally { clearTimeout(timeout) }
}

export interface BreadboardClient {
  invokePublicAction(actionId: PublicActionId, input?: Readonly<Record<string, unknown>>): Promise<unknown>
  health(): Promise<HealthResponse>; engineStatus(): Promise<EngineStatusResponse>
  createSession(payload: SessionCreateRequest): Promise<SessionCreateResponse>; listSessions(): Promise<SessionSummary[]>; getSession(id: string): Promise<SessionSummary>
  listSessionRecords(id: string, options?: { schemaVersion?: string; offset?: number; limit?: number }): Promise<SessionKernelRecordList>
  postInput(id: string, body: SessionInputRequest): Promise<SessionInputResponse>; postCommand(id: string, body: SessionCommandRequest): Promise<SessionCommandResponse>; deleteSession(id: string): Promise<void>
  listSessionFiles(id: string, path?: string): Promise<SessionFileInfo[]>; readSessionFile(id: string, path: string, options?: ReadSessionFileOptions): Promise<SessionFileContent>
  getModelCatalog(configPath: string): Promise<ModelCatalogResponse>; getSkillsCatalog(id: string): Promise<SkillCatalogResponse>; getCtreeSnapshot(id: string): Promise<CTreeSnapshotResponse>
  getCtreeTree(id: string, options?: { stage?: string; includePreviews?: boolean; source?: string }): Promise<CTreeTreeResponse>
  getCtreeDisk(id: string): Promise<CTreeDiskArtifactsResponse>; getCtreeEvents(id: string, options?: { source?: string; offset?: number; limit?: number }): Promise<CTreeEventsResponse>
  getE4Health(): Promise<E4Health>; listE4Schemas(): Promise<E4SchemaList>; listE4Lanes(options?: Record<string, unknown>): Promise<E4LaneList>; getE4Lane(id: string): Promise<E4LaneDetail>; listE4Claims(options?: Record<string, unknown>): Promise<E4ClaimList>; getE4Claim(id: string): Promise<E4ClaimDetail>; getE4Catalog(options?: Record<string, unknown>): Promise<E4CatalogPage>; getE4CatalogBinding(): Promise<E4CatalogBinding>; getE4LedgerRows(options?: Record<string, unknown>): Promise<E4LedgerRows>; listE4Records(schema: string, options?: Record<string, unknown>): Promise<E4RecordList>; reverifyE4Claim(id: string, body?: E4ReverifyRequest): Promise<E4ReverifyResult>; getE4Coverage(target: string): Promise<E4CoverageMatrix>
  listRegistries(): Promise<RegistryList>; getRegistry(id: string): Promise<Record<string, unknown>>
  providerAuthAttach(body: ProviderAuthAttachRequest): Promise<ProviderAuthAttachResponse>; providerAuthDetach(body: ProviderAuthDetachRequest): Promise<ProviderAuthDetachResponse>; providerAuthStatus(): Promise<ProviderAuthStatusResponse>
  listProviders(): Promise<ReadonlyArray<AuthProviderView>>; listCredentials(providerId?: string): Promise<ReadonlyArray<AuthCredentialView>>
  beginLogin(input: BeginAuthLogin): Promise<AuthLoginSession>; getLogin(loginSessionId: string): Promise<AuthLoginSession>
  completeLogin(input: CompleteAuthLogin & { readonly login_session_id: string }): Promise<AuthLoginSession>; cancelLogin(loginSessionId: string): Promise<AuthActionResponse>
  putApiKey(providerId: string, accountLabel: string, input: PutApiKeyInput): Promise<AuthCredentialView>
  logout(credentialRef: string): Promise<AuthActionResponse>; revoke(credentialRef: string): Promise<AuthActionResponse>
  resolveModelRoles(input: ModelRolesResolveInput): Promise<ModelRolesResolveResponse>
  downloadArtifact(id: string, artifact: string): Promise<string>; uploadAttachments(id: string, attachments: ReadonlyArray<AttachmentUploadPayload>): Promise<AttachmentHandle[]>
  eventsSession(id: string, options?: Omit<EventStreamOptions, "config">): AsyncGenerator<SessionEvent, void, void>
}

const action = (config: BreadboardClientConfig, id: PublicActionId, input: Readonly<Record<string, unknown>> = {}): Promise<unknown> => {
  const r = (method: JsonMethod, route: string, options?: Parameters<typeof request>[3]) => request<unknown>(config, route, method, options)
  switch (id) {
    case "public.system.describe": return r("GET", "/v1/system"); case "public.system.health": return r("GET", "/v1/health"); case "public.system.schemas": return r("GET", "/v1/schemas")
    case "public.harness.create": return r("POST", "/v1/harnesses", { body: { directory: input.directory ?? "." } }); case "public.harness.list": return r("GET", "/v1/harnesses"); case "public.harness.get": return r("GET", `/v1/harnesses/${resource(String(input.harness_id ?? ""), "harness_id")}`); case "public.harness.update": return r("PUT", `/v1/harnesses/${resource(String(input.harness_id ?? ""), "harness_id")}`, { body: { definition: input.definition } }); case "public.harness.validate": return r("POST", `/v1/harnesses/${resource(String(input.harness_id ?? ""), "harness_id")}/validate`); case "public.harness.explain": return r("POST", `/v1/harnesses/${resource(String(input.harness_id ?? ""), "harness_id")}/explain`); case "public.harness.lock": return r("POST", `/v1/harnesses/${resource(String(input.harness_id ?? ""), "harness_id")}/lock`); case "public.harness_lock.get": return r("GET", `/v1/harness-locks/${resource(String(input.lock_id ?? ""), "lock_id")}`)
    case "public.integration.list": return r("GET", "/v1/integrations"); case "public.integration.get": return r("GET", `/v1/integrations/${identifier(String(input.integration_id ?? ""), "integration_id")}`); case "public.integration.probe": return r("POST", `/v1/integrations/${identifier(String(input.integration_id ?? ""), "integration_id")}/probe`, { headers: input.idempotency_key ? { "Idempotency-Key": String(input.idempotency_key) } : undefined }); case "public.artifact.list": return r("GET", "/v1/artifacts"); case "public.artifact.get": return r("GET", `/v1/artifacts/${identifier(String(input.artifact_id ?? ""), "artifact_id")}`); case "public.artifact.verify": return r("POST", `/v1/artifacts/${identifier(String(input.artifact_id ?? ""), "artifact_id")}/verify`)
    case "public.session.start": { const body = { ...input }; delete body.idempotency_key; return r("POST", "/v1/sessions", { body, headers: input.idempotency_key ? { "Idempotency-Key": String(input.idempotency_key) } : undefined }) }; case "public.session.list": return r("GET", "/v1/sessions"); case "public.session.get": return r("GET", `/v1/sessions/${identifier(String(input.session_id ?? ""), "session_id")}`); case "public.session.send_input": return r("POST", `/v1/sessions/${identifier(String(input.session_id ?? ""), "session_id")}/input`, { body: { content: input.content }, headers: input.idempotency_key ? { "Idempotency-Key": String(input.idempotency_key) } : undefined }); case "public.session.approve": return r("POST", `/v1/sessions/${identifier(String(input.session_id ?? ""), "session_id")}/approve`, { body: { request_id: input.request_id, decision: input.decision }, headers: input.idempotency_key ? { "Idempotency-Key": String(input.idempotency_key) } : undefined }); case "public.session.resume": return r("POST", `/v1/sessions/${identifier(String(input.session_id ?? ""), "session_id")}/resume`, { headers: input.idempotency_key ? { "Idempotency-Key": String(input.idempotency_key) } : undefined }); case "public.session.cancel": return r("POST", `/v1/sessions/${identifier(String(input.session_id ?? ""), "session_id")}/cancel`, { body: { reason: input.reason ?? "operator request" }, headers: input.idempotency_key ? { "Idempotency-Key": String(input.idempotency_key) } : undefined }); case "public.session.artifacts": return r("GET", `/v1/sessions/${identifier(String(input.session_id ?? ""), "session_id")}/artifacts`)
    case "public.session.events": return Promise.resolve(streamSessionEvents(String(input.session_id ?? ""), { config: config as import("./stream.js").StreamConfig, query: { ...(typeof input.resume_token === "number" ? { resume_token: input.resume_token } : {}), ...(typeof input.limit === "number" ? { limit: input.limit } : {}) }, lastEventId: typeof input.last_event_id === "number" ? String(input.last_event_id) : undefined }))
  }
}

const publicData = async <T>(config: BreadboardClientConfig, route: string, key: string): Promise<T> => {
  const result = await request<unknown>(config, route, "GET")
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const envelope = result as { ok?: unknown; data?: Record<string, unknown> }
    if (envelope.data && typeof envelope.data === "object") {
      const value = envelope.data[key]
      if (value !== undefined) return value as T
      if ("ok" in envelope || "data" in envelope) throw new ApiError(`Public result missing data.${key}`, 502, result)
    }
  }
  return result as T
}
export const createBreadboardClient = (config: BreadboardClientConfig): BreadboardClient => {
  const c = {
    describeSystem: () => action(config, "public.system.describe"), healthSystem: () => action(config, "public.system.health"), schemasSystem: () => action(config, "public.system.schemas"), createHarness: (directory = ".") => action(config, "public.harness.create", { directory }), listHarness: () => action(config, "public.harness.list"), getHarness: (id: string) => action(config, "public.harness.get", { harness_id: id }), updateHarness: (id: string, definition: Record<string, unknown>) => action(config, "public.harness.update", { harness_id: id, definition }), validateHarness: (id: string) => action(config, "public.harness.validate", { harness_id: id }), explainHarness: (id: string) => action(config, "public.harness.explain", { harness_id: id }), lockHarness: (id: string) => action(config, "public.harness.lock", { harness_id: id }), getHarnessLock: (id: string) => action(config, "public.harness_lock.get", { lock_id: id }), listIntegration: () => action(config, "public.integration.list"), getIntegration: (id: string) => action(config, "public.integration.get", { integration_id: id }), probeIntegration: (id: string, key?: string) => action(config, "public.integration.probe", { integration_id: id, idempotency_key: key }), listArtifact: () => action(config, "public.artifact.list"), getArtifact: (id: string) => action(config, "public.artifact.get", { artifact_id: id }), verifyArtifact: (id: string) => action(config, "public.artifact.verify", { artifact_id: id }), startSession: (body: Record<string, unknown>, key?: string) => action(config, "public.session.start", { ...body, idempotency_key: key }), listSession: () => action(config, "public.session.list"), sendInputSession: (id: string, content: string, key?: string) => action(config, "public.session.send_input", { session_id: id, content, idempotency_key: key }), approveSession: (id: string, request: string, decision: string, key?: string) => action(config, "public.session.approve", { session_id: id, request_id: request, decision, idempotency_key: key }), resumeSession: (id: string, key?: string) => action(config, "public.session.resume", { session_id: id, idempotency_key: key }), cancelSession: (id: string, reason?: string, key?: string) => action(config, "public.session.cancel", { session_id: id, reason, idempotency_key: key }), artifactsSession: (id: string) => action(config, "public.session.artifacts", { session_id: id }),
    invokePublicAction: (id: PublicActionId, input?: Readonly<Record<string, unknown>>) => action(config, id, input),
    health: () => request<HealthResponse>(config, "/v1/health", "GET"), engineStatus: () => request<EngineStatusResponse>(config, "/v1/status", "GET"),
    createSession: (body: SessionCreateRequest) => request<SessionCreateResponse>(config, "/v1/sessions", "POST", { body }), listSessions: () => publicData<SessionSummary[]>(config, "/v1/sessions", "sessions"), getSession: (id: string) => publicData<SessionSummary>(config, `/v1/sessions/${encodeURIComponent(id)}`, "session"),
    listSessionRecords: (id: string, o?: { schemaVersion?: string; offset?: number; limit?: number }) => request<SessionKernelRecordList>(config, `/v1/sessions/${encodeURIComponent(id)}/records`, "GET", { query: { schema_version: o?.schemaVersion, offset: o?.offset, limit: o?.limit } }), postInput: (id: string, body: SessionInputRequest) => request<SessionInputResponse>(config, `/v1/sessions/${encodeURIComponent(id)}/input`, "POST", { body }), postCommand: (id: string, body: SessionCommandRequest) => request<SessionCommandResponse>(config, `/v1/sessions/${encodeURIComponent(id)}/command`, "POST", { body }), deleteSession: (id: string) => request<void>(config, `/v1/sessions/${encodeURIComponent(id)}`, "DELETE"),
    listSessionFiles: (id: string, path?: string) => request<SessionFileInfo[]>(config, `/v1/sessions/${encodeURIComponent(id)}/files`, "GET", { query: path ? { path } : undefined }), readSessionFile: (id: string, path: string, o?: ReadSessionFileOptions) => request<SessionFileContent>(config, `/v1/sessions/${encodeURIComponent(id)}/files/content`, "GET", { query: { path, mode: o?.mode ?? "cat", head_lines: o?.headLines, tail_lines: o?.tailLines, max_bytes: o?.maxBytes } }), getModelCatalog: (path: string) => request<ModelCatalogResponse>(config, "/v1/models", "GET", { query: { config_path: path } }), getSkillsCatalog: (id: string) => request<SkillCatalogResponse>(config, `/v1/sessions/${encodeURIComponent(id)}/skills`, "GET"), getCtreeSnapshot: (id: string) => request<CTreeSnapshotResponse>(config, `/v1/sessions/${encodeURIComponent(id)}/ctrees`, "GET"),
    getCtreeTree: (id: string, o?: { stage?: string; includePreviews?: boolean; source?: string }) => request<CTreeTreeResponse>(config, `/sessions/${encodeURIComponent(id)}/ctrees/tree`, "GET", { query: { stage: o?.stage, include_previews: o?.includePreviews, source: o?.source } }), getCtreeDisk: (id: string) => request<CTreeDiskArtifactsResponse>(config, `/sessions/${encodeURIComponent(id)}/ctrees/disk`, "GET"), getCtreeEvents: (id: string, o?: { source?: string; offset?: number; limit?: number }) => request<CTreeEventsResponse>(config, `/sessions/${encodeURIComponent(id)}/ctrees/events`, "GET", { query: { source: o?.source, offset: o?.offset, limit: o?.limit } }),
    getE4Health: () => request<E4Health>(config, "/v1/e4/health", "GET"), listE4Schemas: () => request<E4SchemaList>(config, "/v1/e4/schemas", "GET"), listE4Lanes: (o?: Record<string, unknown>) => request<E4LaneList>(config, "/v1/e4/lanes", "GET", { query: { phase: o?.phase as string, kind: o?.kind as string, target_family: o?.targetFamily as string, status: o?.status as string, offset: o?.offset as number, limit: o?.limit as number } }), getE4Lane: (id: string) => request<E4LaneDetail>(config, `/v1/e4/lanes/${encodeURIComponent(id)}`, "GET"), listE4Claims: (o?: Record<string, unknown>) => request<E4ClaimList>(config, "/v1/e4/claims", "GET", { query: { accepted: o?.accepted as boolean, target_family: o?.targetFamily as string, kind: o?.kind as string, offset: o?.offset as number, limit: o?.limit as number } }), getE4Claim: (id: string) => request<E4ClaimDetail>(config, `/v1/e4/claims/${encodeURIComponent(id)}`, "GET"), getE4Catalog: (o?: Record<string, unknown>) => request<E4CatalogPage>(config, "/v1/e4/catalog", "GET", { query: { lane_id: o?.laneId as string, artifact_kind: o?.artifactKind as string, offset: o?.offset as number, limit: o?.limit as number } }), getE4CatalogBinding: () => request<E4CatalogBinding>(config, "/v1/e4/catalog/binding", "GET"), getE4LedgerRows: (o?: Record<string, unknown>) => request<E4LedgerRows>(config, "/v1/e4/ledger/rows", "GET", { query: { feature_id: o?.featureId as string, lane_id: o?.laneId as string, offset: o?.offset as number, limit: o?.limit as number } }), listE4Records: (schema: string, o?: Record<string, unknown>) => request<E4RecordList>(config, "/v1/e4/records", "GET", { query: { schema_version: schema, lane_id: o?.laneId as string, source: o?.source as string, offset: o?.offset as number, limit: o?.limit as number } }), reverifyE4Claim: (id: string, body: E4ReverifyRequest = {}) => request<E4ReverifyResult>(config, `/v1/e4/claims/${encodeURIComponent(id)}/reverify`, "POST", { body }), getE4Coverage: (target: string) => request<E4CoverageMatrix>(config, `/v1/e4/coverage/${encodeURIComponent(target)}`, "GET"),
    listRegistries: () => request<RegistryList>(config, "/v1/registries", "GET"), getRegistry: (id: string) => request<Record<string, unknown>>(config, `/v1/registries/${encodeURIComponent(id)}`, "GET"), submitRlRun: (body: RLRunSubmitRequest) => request<RLRunSubmitResponse>(config, "/v1/rl/runs", "POST", { body }), getRlRun: (id: string, tenant: string, workspace: string) => request<RLRunStatusResponse>(config, `/v1/rl/runs/${encodeURIComponent(id)}`, "GET", { query: { tenant_id: tenant, workspace_id: workspace } }), getRlRunEvents: (id: string, tenant: string, workspace: string, from = 0) => request<string>(config, `/v1/rl/runs/${encodeURIComponent(id)}/events`, "GET", { query: { tenant_id: tenant, workspace_id: workspace, from_sequence: from }, responseType: "text" }), cancelRlRun: (id: string, tenant: string, workspace: string, reason?: string) => request<RLRunStatusResponse>(config, `/v1/rl/runs/${encodeURIComponent(id)}/cancel`, "POST", { body: { tenant_id: tenant, workspace_id: workspace, reason } satisfies RLRunCancelRequest }), listRlArtifacts: (id: string, tenant: string, workspace: string) => request<RLRunArtifactListResponse>(config, `/v1/rl/runs/${encodeURIComponent(id)}/artifacts`, "GET", { query: { tenant_id: tenant, workspace_id: workspace } }), replayRlArtifact: (id: string, artifact: string, tenant: string, workspace: string) => request<RLRunReplayResponse>(config, `/v1/rl/runs/${encodeURIComponent(id)}/replay/${encodeURIComponent(artifact)}`, "GET", { query: { tenant_id: tenant, workspace_id: workspace } }), getRlAudit: (id: string, tenant: string, workspace: string) => request<RLRunAuditResponse>(config, `/v1/rl/runs/${encodeURIComponent(id)}/audit`, "GET", { query: { tenant_id: tenant, workspace_id: workspace } }),
    providerAuthAttach: (body: ProviderAuthAttachRequest) => request<ProviderAuthAttachResponse>(config, "/v1/provider-auth/attach", "POST", { body }), providerAuthDetach: (body: ProviderAuthDetachRequest) => request<ProviderAuthDetachResponse>(config, "/v1/provider-auth/detach", "POST", { body }), providerAuthStatus: () => request<ProviderAuthStatusResponse>(config, "/v1/provider-auth/status", "GET"),
    listProviders: () => request<ReadonlyArray<AuthProviderView>>(config, "/v1/auth/providers", "GET"),
    listCredentials: (providerId?: string) => request<ReadonlyArray<AuthCredentialView>>(config, "/v1/auth/credentials", "GET", { query: { provider_id: providerId } }),
    beginLogin: (input: BeginAuthLogin) => request<AuthLoginSession>(config, "/v1/auth/login-sessions", "POST", { body: input }),
    getLogin: (loginSessionId: string) => request<AuthLoginSession>(config, `/v1/auth/login-sessions/${encodeURIComponent(loginSessionId)}`, "GET"),
    completeLogin: (input: CompleteAuthLogin) => request<AuthLoginSession>(config, `/v1/auth/login-sessions/${encodeURIComponent(input.login_session_id)}/complete`, "POST", { body: { authorization_code: input.authorization_code, code: input.code, state: input.state, callback_url: input.callback_url, account_label: input.account_label, alias: input.alias } }),
    cancelLogin: (loginSessionId: string) => request<AuthActionResponse>(config, `/v1/auth/login-sessions/${encodeURIComponent(loginSessionId)}`, "DELETE"),
    putApiKey: (providerId: string, accountLabel: string, input: PutApiKeyInput) => request<AuthCredentialView>(config, `/v1/auth/credentials/${encodeURIComponent(providerId)}/${encodeURIComponent(accountLabel)}/api-key`, "PUT", { body: input }),
    logout: (credentialRef: string) => request<AuthActionResponse>(config, `/v1/auth/credentials/${encodeURIComponent(credentialRef)}`, "DELETE"),
    revoke: (credentialRef: string) => request<AuthActionResponse>(config, `/v1/auth/credentials/${encodeURIComponent(credentialRef)}/revoke`, "POST"),
    resolveModelRoles: (input: ModelRolesResolveInput) => request<ModelRolesResolveResponse>(config, "/v1/model-roles/resolve", "POST", { body: input }),
    downloadArtifact: (id: string, artifact: string) => request<string>(config, `/v1/sessions/${encodeURIComponent(id)}/download`, "GET", { query: { artifact }, responseType: "text" }),
    uploadAttachments: async (id: string, attachments: ReadonlyArray<AttachmentUploadPayload>) => { if (!attachments.length) return []; const form = new FormData(); attachments.forEach((a, i) => { const bytes = Uint8Array.from(atob(a.base64), (char) => char.charCodeAt(0)); form.append("files", new Blob([bytes], { type: a.mime || "application/octet-stream" }), a.filename ?? `attachment-${i + 1}.bin`) }); form.append("metadata", JSON.stringify({ source: "clipboard" })); const token = await valueToken(config); const response = await (config.fetch ?? globalThis.fetch)(buildUrl(config.baseUrl, `/v1/sessions/${encodeURIComponent(id)}/attachments`), { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : undefined, body: form }); const content = response.headers.get("content-type") ?? ""; const payload = content.includes("json") ? await response.json() : undefined; if (!response.ok) throw new ApiError(`Attachment upload failed with status ${response.status}`, response.status, payload); return (payload as { attachments?: AttachmentHandle[] } | undefined)?.attachments ?? [] },
    eventsSession: (id: string, options: Omit<EventStreamOptions, "config"> = {}) => streamSessionEvents(id, { ...options, config: config as import("./stream.js").StreamConfig }),
  }
  return c as BreadboardClient
}
export const createApiClient = createBreadboardClient
export type { PublicResult }
