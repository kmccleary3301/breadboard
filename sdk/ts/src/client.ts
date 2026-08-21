import { streamSessionEvents, type EventStreamOptions } from "./stream.js"
import type { PublicResult } from "./types.js"

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

type JsonMethod = "GET" | "POST" | "PUT" | "DELETE"

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
const resourcePath = (value: string): string => {
  const parts = value.split("/")
  if (parts.some((part) => part === "" || part === "." || part === "..")) {
    throw new Error("resource identifiers cannot contain empty or dot segments")
  }
  return parts.map(encodeURIComponent).join("/")
}
const idempotencyHeaders = (key?: string): Record<string, string> | undefined =>
  key ? { "Idempotency-Key": key } : undefined

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
  describeSystem: () => requestWithConfig<PublicResult>(config, "/v1/system", "GET"),
  healthSystem: () => requestWithConfig<PublicResult>(config, "/v1/health", "GET"),
  schemasSystem: () => requestWithConfig<PublicResult>(config, "/v1/schemas", "GET"),
  createHarness: (directory = ".") => requestWithConfig<PublicResult>(config, "/v1/harnesses", "POST", { body: { directory } }),
  listHarness: () => requestWithConfig<PublicResult>(config, "/v1/harnesses", "GET"),
  getHarness: (harnessId: string) => requestWithConfig<PublicResult>(config, `/v1/harnesses/${resourcePath(harnessId)}`, "GET"),
  updateHarness: (harnessId: string, definition: Record<string, unknown>) =>
    requestWithConfig<PublicResult>(config, `/v1/harnesses/${resourcePath(harnessId)}`, "PUT", { body: { definition } }),
  validateHarness: (harnessId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/harnesses/${resourcePath(harnessId)}/validate`, "POST"),
  explainHarness: (harnessId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/harnesses/${resourcePath(harnessId)}/explain`, "POST"),
  lockHarness: (harnessId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/harnesses/${resourcePath(harnessId)}/lock`, "POST"),
  getHarnessLock: (lockId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/harness-locks/${resourcePath(lockId)}`, "GET"),
  listIntegration: () => requestWithConfig<PublicResult>(config, "/v1/integrations", "GET"),
  getIntegration: (integrationId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/integrations/${encodeURIComponent(integrationId)}`, "GET"),
  probeIntegration: (integrationId: string, idempotencyKey?: string) =>
    requestWithConfig<PublicResult>(config, `/v1/integrations/${encodeURIComponent(integrationId)}/probe`, "POST", {
      headers: idempotencyHeaders(idempotencyKey),
    }),
  listArtifact: () => requestWithConfig<PublicResult>(config, "/v1/artifacts", "GET"),
  getArtifact: (artifactId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/artifacts/${encodeURIComponent(artifactId)}`, "GET"),
  verifyArtifact: (artifactId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/artifacts/${encodeURIComponent(artifactId)}/verify`, "POST"),
  startSession: (payload: Record<string, unknown>, idempotencyKey?: string) =>
    requestWithConfig<PublicResult>(config, "/v1/sessions", "POST", {
      body: payload,
      headers: idempotencyHeaders(idempotencyKey),
    }),
  listSession: () => requestWithConfig<PublicResult>(config, "/v1/sessions", "GET"),
  getSession: (sessionId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/sessions/${encodeURIComponent(sessionId)}`, "GET"),
  sendInputSession: (sessionId: string, content: string, idempotencyKey?: string) =>
    requestWithConfig<PublicResult>(config, `/v1/sessions/${encodeURIComponent(sessionId)}/input`, "POST", {
      body: { content },
      headers: idempotencyHeaders(idempotencyKey),
    }),
  approveSession: (sessionId: string, requestId: string, decision: string, idempotencyKey?: string) =>
    requestWithConfig<PublicResult>(config, `/v1/sessions/${encodeURIComponent(sessionId)}/approve`, "POST", {
      body: { request_id: requestId, decision },
      headers: idempotencyHeaders(idempotencyKey),
    }),
  resumeSession: (sessionId: string, idempotencyKey?: string) =>
    requestWithConfig<PublicResult>(config, `/v1/sessions/${encodeURIComponent(sessionId)}/resume`, "POST", {
      headers: idempotencyHeaders(idempotencyKey),
    }),
  cancelSession: (sessionId: string, reason = "operator request", idempotencyKey?: string) =>
    requestWithConfig<PublicResult>(config, `/v1/sessions/${encodeURIComponent(sessionId)}/cancel`, "POST", {
      body: { reason },
      headers: idempotencyHeaders(idempotencyKey),
    }),
  artifactsSession: (sessionId: string) =>
    requestWithConfig<PublicResult>(config, `/v1/sessions/${encodeURIComponent(sessionId)}/artifacts`, "GET"),
  eventsSession: (sessionId: string, options: Omit<EventStreamOptions, "config"> = {}) =>
    streamSessionEvents(sessionId, { ...options, config }),
})

