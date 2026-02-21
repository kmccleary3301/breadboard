import type {
  CTreeSnapshotResponse,
  HealthResponse,
  ModelCatalogResponse,
  SessionCommandRequest,
  SessionCommandResponse,
  SessionCreateRequest,
  SessionCreateResponse,
  SessionFileContent,
  SessionFileInfo,
  SessionInputRequest,
  SessionInputResponse,
  SessionSummary,
  SkillCatalogResponse,
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

export interface ReadSessionFileOptions {
  readonly mode?: "cat" | "snippet"
  readonly headLines?: number
  readonly tailLines?: number
  readonly maxBytes?: number
}

export interface BreadboardClientConfig {
  readonly baseUrl: string
  readonly authToken?: string
  readonly requestTimeoutMs?: number
}

interface RequestOptions {
  body?: unknown
  query?: Record<string, string | number | boolean | undefined>
  responseType?: "json" | "text"
  headers?: Record<string, string>
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
  config: BreadboardClientConfig,
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

export const createBreadboardClient = (config: BreadboardClientConfig) => ({
  health: () => requestWithConfig<HealthResponse>(config, "/health", "GET"),
  createSession: (payload: SessionCreateRequest) =>
    requestWithConfig<SessionCreateResponse>(config, "/sessions", "POST", { body: payload }),
  listSessions: () => requestWithConfig<SessionSummary[]>(config, "/sessions", "GET"),
  getSession: (sessionId: string) => requestWithConfig<SessionSummary>(config, `/sessions/${sessionId}`, "GET"),
  postInput: (sessionId: string, body: SessionInputRequest) =>
    requestWithConfig<SessionInputResponse>(config, `/sessions/${sessionId}/input`, "POST", { body }),
  postCommand: (sessionId: string, body: SessionCommandRequest) =>
    requestWithConfig<SessionCommandResponse>(config, `/sessions/${sessionId}/command`, "POST", { body }),
  deleteSession: (sessionId: string) => requestWithConfig<void>(config, `/sessions/${sessionId}`, "DELETE"),
  listSessionFiles: (sessionId: string, path?: string) =>
    requestWithConfig<SessionFileInfo[]>(config, `/sessions/${sessionId}/files`, "GET", {
      query: path ? { path } : undefined,
    }),
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
  getSkillsCatalog: (sessionId: string) => requestWithConfig<SkillCatalogResponse>(config, `/sessions/${sessionId}/skills`, "GET"),
  getCtreeSnapshot: (sessionId: string) => requestWithConfig<CTreeSnapshotResponse>(config, `/sessions/${sessionId}/ctrees`, "GET"),
})
