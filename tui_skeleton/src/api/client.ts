import { loadAppConfig } from "../config/appConfig.js"
import type {
  ErrorResponse,
  SessionCreateRequest,
  SessionCreateResponse,
  SessionEvent,
  SessionSummary,
  SessionFileInfo,
  SessionFileContent,
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

const request = async <T>(path: string, method: JsonMethod, options: RequestOptions = {}): Promise<T> => {
  const config = loadAppConfig()
  const url = buildUrl(config.baseUrl, path, options.query)
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs)
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ?? {}),
  }
  if (config.authToken) {
    headers.Authorization = `Bearer ${config.authToken}`
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

export const ApiClient = {
  createSession: (payload: SessionCreateRequest) => request<SessionCreateResponse>("/sessions", "POST", { body: payload }),
  listSessions: () => request<SessionSummary[]>("/sessions", "GET"),
  getSession: (sessionId: string) => request<SessionSummary>(`/sessions/${sessionId}`, "GET"),
  postInput: (sessionId: string, body: { content: string; attachments?: ReadonlyArray<string> }) =>
    request<void>(`/sessions/${sessionId}/input`, "POST", { body }),
  postCommand: (sessionId: string, body: Record<string, unknown>) => request<void>(`/sessions/${sessionId}/command`, "POST", { body }),
  deleteSession: (sessionId: string) => request<void>(`/sessions/${sessionId}`, "DELETE"),
  listSessionFiles: (sessionId: string, path?: string) =>
    request<SessionFileInfo[]>(`/sessions/${sessionId}/files`, "GET", { query: path ? { path } : undefined }),
  readSessionFile: (sessionId: string, filePath: string, options?: ReadSessionFileOptions) =>
    request<SessionFileContent>(`/sessions/${sessionId}/files`, "GET", {
      query: {
        path: filePath,
        mode: options?.mode ?? "cat",
        head_lines: options?.headLines,
        tail_lines: options?.tailLines,
        max_bytes: options?.maxBytes,
      },
    }),
  getModelCatalog: (configPath: string) =>
    request<ModelCatalogResponse>("/models", "GET", { query: { config_path: configPath } }),
  getSkillsCatalog: (sessionId: string) =>
    request<SkillCatalogResponse>(`/sessions/${sessionId}/skills`, "GET"),
  getCtreeSnapshot: (sessionId: string) =>
    request<CTreeSnapshotResponse>(`/sessions/${sessionId}/ctrees`, "GET"),
  downloadArtifact: (sessionId: string, artifact: string) =>
    request<string>(`/sessions/${sessionId}/download`, "GET", { query: { artifact }, responseType: "text" }),
  uploadAttachments: async (sessionId: string, attachments: ReadonlyArray<AttachmentUploadPayload>) => {
    if (attachments.length === 0) return []
    const config = loadAppConfig()
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
    if (config.authToken) {
      headers.Authorization = `Bearer ${config.authToken}`
    }
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs)
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
}

export type {
  SessionCreateRequest,
  SessionCreateResponse,
  SessionEvent,
  SessionSummary,
  ErrorResponse,
  SessionFileInfo,
  SessionFileContent,
  ModelCatalogResponse,
  SkillCatalogResponse,
  CTreeSnapshotResponse,
}
