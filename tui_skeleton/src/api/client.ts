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

type JsonMethod = "GET" | "POST" | "PUT"

interface RequestOptions {
  readonly body?: unknown
  readonly headers?: Readonly<Record<string, string>>
}

export interface ApiClientConfig {
  readonly baseUrl: string
  readonly authToken?: string | (() => Promise<string | undefined>)
  readonly requestTimeoutMs?: number
}

export type PublicActionId =
  | "public.system.describe"
  | "public.system.health"
  | "public.system.schemas"
  | "public.harness.create"
  | "public.harness.list"
  | "public.harness.get"
  | "public.harness.update"
  | "public.harness.validate"
  | "public.harness.explain"
  | "public.harness.lock"
  | "public.harness_lock.get"
  | "public.integration.list"
  | "public.integration.get"
  | "public.integration.probe"
  | "public.artifact.list"
  | "public.artifact.get"
  | "public.artifact.verify"
  | "public.session.start"
  | "public.session.list"
  | "public.session.get"
  | "public.session.send_input"
  | "public.session.approve"
  | "public.session.resume"
  | "public.session.cancel"
  | "public.session.events"
  | "public.session.artifacts"

export interface ApiClientInstance {
  invokePublicAction(
    actionId: PublicActionId,
    input?: Readonly<Record<string, unknown>>,
  ): Promise<unknown>
}

const resolveAuthToken = async (config: ApiClientConfig): Promise<string | undefined> =>
  typeof config.authToken === "function" ? await config.authToken() : config.authToken

const requestWithConfig = async (
  config: ApiClientConfig,
  route: string,
  method: JsonMethod,
  options: RequestOptions = {},
): Promise<unknown> => {
  const url = new URL(route.replace(/^\/+/, ""), config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`)
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs ?? 30_000)
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...options.headers,
  }
  try {
    const token = await resolveAuthToken(config)
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    })
    const contentType = response.headers.get("content-type") ?? ""
    const isJson = contentType.includes("application/json")
    const payload = isJson
      ? await response.json().catch(() => undefined)
      : await response.text().catch(() => undefined)
    if (!response.ok) {
      throw new ApiError(`Request failed with status ${response.status}`, response.status, payload)
    }
    return payload
  } finally {
    clearTimeout(timeout)
  }
}

const requiredString = (input: Readonly<Record<string, unknown>>, name: string): string => {
  const value = input[name]
  if (typeof value !== "string" || value.length === 0) throw new Error(`${name} is required`)
  return value
}

const identifier = (input: Readonly<Record<string, unknown>>, name: string): string =>
  encodeURIComponent(requiredString(input, name))

const resource = (input: Readonly<Record<string, unknown>>, name: string): string => {
  const parts = requiredString(input, name).split("/")
  if (parts.some((part) => part === "" || part === "." || part === "..")) {
    throw new Error(`${name} cannot contain empty or dot segments`)
  }
  return parts.map(encodeURIComponent).join("/")
}

const idempotencyHeaders = (
  input: Readonly<Record<string, unknown>>,
): Readonly<Record<string, string>> | undefined =>
  typeof input.idempotency_key === "string" && input.idempotency_key.length > 0
    ? { "Idempotency-Key": input.idempotency_key }
    : undefined

const invokePublicAction = async (
  config: ApiClientConfig,
  actionId: PublicActionId,
  input: Readonly<Record<string, unknown>> = {},
): Promise<unknown> => {
  const request = (method: JsonMethod, route: string, options?: RequestOptions) =>
    requestWithConfig(config, route, method, options)
  switch (actionId) {
    case "public.system.describe": return request("GET", "/v1/system")
    case "public.system.health": return request("GET", "/v1/health")
    case "public.system.schemas": return request("GET", "/v1/schemas")
    case "public.harness.create": return request("POST", "/v1/harnesses", { body: { directory: input.directory ?? "." } })
    case "public.harness.list": return request("GET", "/v1/harnesses")
    case "public.harness.get": return request("GET", `/v1/harnesses/${resource(input, "harness_id")}`)
    case "public.harness.update": return request("PUT", `/v1/harnesses/${resource(input, "harness_id")}`, { body: { definition: input.definition } })
    case "public.harness.validate": return request("POST", `/v1/harnesses/${resource(input, "harness_id")}/validate`)
    case "public.harness.explain": return request("POST", `/v1/harnesses/${resource(input, "harness_id")}/explain`)
    case "public.harness.lock": return request("POST", `/v1/harnesses/${resource(input, "harness_id")}/lock`)
    case "public.harness_lock.get": return request("GET", `/v1/harness-locks/${resource(input, "lock_id")}`)
    case "public.integration.list": return request("GET", "/v1/integrations")
    case "public.integration.get": return request("GET", `/v1/integrations/${identifier(input, "integration_id")}`)
    case "public.integration.probe": return request("POST", `/v1/integrations/${identifier(input, "integration_id")}/probe`, { headers: idempotencyHeaders(input) })
    case "public.artifact.list": return request("GET", "/v1/artifacts")
    case "public.artifact.get": return request("GET", `/v1/artifacts/${identifier(input, "artifact_id")}`)
    case "public.artifact.verify": return request("POST", `/v1/artifacts/${identifier(input, "artifact_id")}/verify`)
    case "public.session.start": {
      const body = { ...input }
      delete body.idempotency_key
      return request("POST", "/v1/sessions", { body, headers: idempotencyHeaders(input) })
    }
    case "public.session.list": return request("GET", "/v1/sessions")
    case "public.session.get": return request("GET", `/v1/sessions/${identifier(input, "session_id")}`)
    case "public.session.send_input": return request("POST", `/v1/sessions/${identifier(input, "session_id")}/input`, { body: { content: input.content }, headers: idempotencyHeaders(input) })
    case "public.session.approve": return request("POST", `/v1/sessions/${identifier(input, "session_id")}/approve`, { body: { request_id: input.request_id, decision: input.decision }, headers: idempotencyHeaders(input) })
    case "public.session.resume": return request("POST", `/v1/sessions/${identifier(input, "session_id")}/resume`, { headers: idempotencyHeaders(input) })
    case "public.session.cancel": return request("POST", `/v1/sessions/${identifier(input, "session_id")}/cancel`, { body: { reason: input.reason ?? "operator request" }, headers: idempotencyHeaders(input) })
    case "public.session.events": {
      const sessionId = requiredString(input, "session_id")
      const query: Record<string, number> = {}
      if (typeof input.resume_token === "number") query.resume_token = input.resume_token
      if (typeof input.limit === "number") query.limit = input.limit
      const lastEventId = typeof input.last_event_id === "number" ? String(input.last_event_id) : undefined
      const { streamSessionEvents } = await import("./stream.js")
      return streamSessionEvents(sessionId, { config, query, lastEventId })
    }
    case "public.session.artifacts": return request("GET", `/v1/sessions/${identifier(input, "session_id")}/artifacts`)
  }
}

export const createApiClient = (config: ApiClientConfig): ApiClientInstance => ({
  invokePublicAction: (actionId, input) => invokePublicAction(config, actionId, input),
})
