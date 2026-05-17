import fs from "node:fs"
import { homedir } from "node:os"
import path from "node:path"
import { loadAppConfig, type AppConfig, DEFAULT_CONFIG_PATH } from "../config/appConfig.js"
import { resolveAuthToken } from "../config/authTokenProvider.js"
import type {
  ErrorResponse,
  SessionCreateRequest,
  SessionCreateResponse,
  SessionEvent,
  SessionSummary,
  SessionFileInfo,
  SessionFileContent,
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

const CODEX_AUTH_PATH = path.join(homedir(), ".codex", "auth.json")
const OPENAI_PROVIDER_ID = "openai"
const OPENROUTER_PROVIDER_ID = "openrouter"

const buildUrl = (baseUrl: string, path: string, query?: RequestOptions["query"]): URL => {
  const url = new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue
      url.searchParams.set(key, String(value))
    }
  }
  return url
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
  const authToken =
    typeof config.authToken === "function" ? await config.authToken() : config.authToken
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ?? {}),
  }
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`
  }
  try {
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
      throw new ApiError(`Request failed with status ${response.status}`, response.status, payload)
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

export const createApiClient = (config: ApiClientConfig) => ({
  health: () => requestWithConfig<HealthResponse>(config, "/health", "GET"),
  engineStatus: () => requestWithConfig<EngineStatusResponse>(config, "/status", "GET"),
  createSession: (payload: SessionCreateRequest) =>
    requestWithConfig<SessionCreateResponse>(config, "/sessions", "POST", { body: payload }),
  listSessions: () => requestWithConfig<SessionSummary[]>(config, "/sessions", "GET"),
  getSession: (sessionId: string) => requestWithConfig<SessionSummary>(config, `/sessions/${sessionId}`, "GET"),
  postInput: (sessionId: string, body: { content: string; attachments?: ReadonlyArray<string> }) =>
    requestWithConfig<void>(config, `/sessions/${sessionId}/input`, "POST", { body }),
  postCommand: (sessionId: string, body: Record<string, unknown>) =>
    requestWithConfig<void>(config, `/sessions/${sessionId}/command`, "POST", { body }),
  deleteSession: (sessionId: string) => requestWithConfig<void>(config, `/sessions/${sessionId}`, "DELETE"),
  listSessionFiles: (sessionId: string, path?: string) =>
    requestWithConfig<SessionFileInfo[]>(config, `/sessions/${sessionId}/files`, "GET", { query: path ? { path } : undefined }),
  readSessionFile: (sessionId: string, filePath: string, options?: ReadSessionFileOptions) =>
    requestWithConfig<SessionFileContent>(config, `/sessions/${sessionId}/files`, "GET", {
      query: {
        path: filePath,
        mode: options?.mode ?? "cat",
        head_lines: options?.headLines,
        tail_lines: options?.tailLines,
        max_bytes: options?.maxBytes,
      },
    }),
  getModelCatalog: (configPath: string) =>
    requestWithConfig<ModelCatalogResponse>(config, "/models", "GET", { query: { config_path: configPath } }),
  getSkillsCatalog: (sessionId: string) =>
    requestWithConfig<SkillCatalogResponse>(config, `/sessions/${sessionId}/skills`, "GET"),
  getCtreeSnapshot: (sessionId: string) =>
    requestWithConfig<CTreeSnapshotResponse>(config, `/sessions/${sessionId}/ctrees`, "GET"),
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
  providerAuthAttach: (body: ProviderAuthAttachRequest) =>
    requestWithConfig<ProviderAuthAttachResponse>(config, "/v1/provider-auth/attach", "POST", { body }),
  providerAuthDetach: (body: ProviderAuthDetachRequest) =>
    requestWithConfig<ProviderAuthDetachResponse>(config, "/v1/provider-auth/detach", "POST", { body }),
  providerAuthStatus: () =>
    requestWithConfig<ProviderAuthStatusResponse>(config, "/v1/provider-auth/status", "GET"),
  downloadArtifact: (sessionId: string, artifact: string) =>
    requestWithConfig<string>(config, `/sessions/${sessionId}/download`, "GET", { query: { artifact }, responseType: "text" }),
  uploadAttachments: async (sessionId: string, attachments: ReadonlyArray<AttachmentUploadPayload>) => {
    if (attachments.length === 0) return []
    const url = buildUrl(config.baseUrl, `/sessions/${sessionId}/attachments`)
    const form = new FormData()
    attachments.forEach((attachment, index) => {
      const buffer = Buffer.from(attachment.base64, "base64")
      const blob = new Blob([buffer], { type: attachment.mime || "application/octet-stream" })
      const filename = attachment.filename ?? `attachment-${index + 1}.bin`
      form.append("files", blob, filename)
    })
    form.append("metadata", JSON.stringify({ source: "clipboard" }))
    const headers: Record<string, string> = {}
    const authToken =
      typeof config.authToken === "function" ? await config.authToken() : config.authToken
    if (authToken) {
      headers.Authorization = `Bearer ${authToken}`
    }
    const controller = new AbortController()
    const timeoutMs = config.requestTimeoutMs ?? 30_000
    const timeout = setTimeout(() => controller.abort(), timeoutMs)
    try {
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

type ApiClientInstance = ReturnType<typeof createApiClient>

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
  createSession: async (...args) => {
    await ensureLocalProviderAuth(args[0]?.config_path)
    return buildApiClient().createSession(...args)
  },
  listSessions: (...args) => buildApiClient().listSessions(...args),
  getSession: (...args) => buildApiClient().getSession(...args),
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
  ErrorResponse,
  SessionFileInfo,
  SessionFileContent,
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
}
