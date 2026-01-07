import { loadAppConfig, type AppConfig } from "../config/appConfig.js"
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
  ModelCatalogResponse,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
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

export const createApiClient = (config: ApiClientConfig) => ({
  health: () => requestWithConfig<HealthResponse>(config, "/health", "GET"),
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
  createSession: (...args) => buildApiClient().createSession(...args),
  listSessions: (...args) => buildApiClient().listSessions(...args),
  getSession: (...args) => buildApiClient().getSession(...args),
  postInput: (...args) => buildApiClient().postInput(...args),
  postCommand: (...args) => buildApiClient().postCommand(...args),
  deleteSession: (...args) => buildApiClient().deleteSession(...args),
  listSessionFiles: (...args) => buildApiClient().listSessionFiles(...args),
  readSessionFile: (...args) => buildApiClient().readSessionFile(...args),
  getModelCatalog: (...args) => buildApiClient().getModelCatalog(...args),
  getSkillsCatalog: (...args) => buildApiClient().getSkillsCatalog(...args),
  getCtreeSnapshot: (...args) => buildApiClient().getCtreeSnapshot(...args),
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
  ModelCatalogResponse,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
}
