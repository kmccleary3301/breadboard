export const ENGINE_IDENTITY_SCHEMA_VERSION = "bb.engine_identity.v1" as const
export const ENGINE_OWNER_SCHEMA_VERSION = "bb.engine_owner.v1" as const
export const ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION = "bb.engine_client_registration.v1" as const
export const ENGINE_DRAIN_CONTROL_SCHEMA_VERSION = "bb.engine_drain_control.v1" as const
export const P30_SESSION_CONTRACT_ID = "p30-e4-session-v1" as const
export const P30_SESSION_SCHEMA_SHA256 =
  "sha256:5757652c22d6aa2eb7a1cc8be1a40021d3f6a15df18d69ca22dc1916a400dbd4" as const

const ENGINE_PROTOCOL_VERSION = "1.0" as const
const OWNER_LEASE_TTL_SECONDS = 30 as const
const OWNER_RENEWAL_INTERVAL_SECONDS = 10 as const
const REDACTED_BODY = "[redacted]" as const

export type LifecycleMode = "local-owned" | "local-external" | "remote"
export type GracefulControlOutcome = "accepted" | "definitive_rejection" | "timeout" | "uncertain"

export interface LifecycleSessionContractExpectation {
  readonly contractId: typeof P30_SESSION_CONTRACT_ID
  readonly schemaSha256: typeof P30_SESSION_SCHEMA_SHA256
}

export type LifecycleBearerTokenResolver = () =>
  | string
  | undefined
  | Promise<string | undefined>

interface LifecycleE4ClientConfigBase {
  readonly baseUrl: string
  readonly fetch?: typeof fetch
  readonly timeoutMs?: number
  readonly expectedSessionContract: LifecycleSessionContractExpectation
}

export type LifecycleE4ClientConfig = LifecycleE4ClientConfigBase & (
  | {
      readonly bearerToken?: string
      readonly bearerTokenResolver?: never
    }
  | {
      readonly bearerToken?: never
      readonly bearerTokenResolver?: LifecycleBearerTokenResolver
    }
)

export interface LifecycleFailureCorrelation {
  readonly requestId?: string
  readonly correlationId?: string
}

export interface LifecycleHttpFailureEvidence {
  readonly status: number
  readonly code: string | null
  readonly correlation: LifecycleFailureCorrelation
  readonly body: typeof REDACTED_BODY
}

export type LifecycleE4Failure =
  | ({ readonly kind: "identity-changed" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "session-schema-mismatch" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "registration-schema-mismatch" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "control-schema-mismatch" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "owner-conflict" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "owner-expired" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "registration-conflict" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "registration-expired" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "drain-conflict" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "recovery-failed" } & LifecycleHttpFailureEvidence)
  | ({ readonly kind: "auth" } & LifecycleHttpFailureEvidence)
  | { readonly kind: "tls"; readonly code: "tls_transport_error" }
  | ({ readonly kind: "redirect" } & LifecycleHttpFailureEvidence)
  | { readonly kind: "timeout" }
  | { readonly kind: "caller-abort" }
  | ({ readonly kind: "http" } & LifecycleHttpFailureEvidence)
  | { readonly kind: "protocol"; readonly code: string }
  | ({ readonly kind: "protocol"; readonly code: string } & Omit<LifecycleHttpFailureEvidence, "code">)

const failureMessage = (failure: LifecycleE4Failure): string => {
  switch (failure.kind) {
    case "identity-changed": return "Engine identity changed"
    case "session-schema-mismatch": return "Session contract mismatch"
    case "registration-schema-mismatch": return "Registration schema mismatch"
    case "control-schema-mismatch": return "Control schema mismatch"
    case "owner-conflict": return "Engine owner conflict"
    case "owner-expired": return "Engine owner lease expired"
    case "registration-conflict": return "Client registration conflict"
    case "registration-expired": return "Client registration expired"
    case "drain-conflict": return "Engine drain conflict"
    case "recovery-failed": return "Engine drain recovery failed"
    case "auth": return "Lifecycle authorization failed"
    case "tls": return "TLS transport failed"
    case "redirect": return "Lifecycle endpoint redirected"
    case "timeout": return "Lifecycle request timed out"
    case "caller-abort": return "Lifecycle request aborted by caller"
    case "http": return `Lifecycle HTTP request failed (${failure.status})`
    case "protocol": return `Lifecycle protocol error (${failure.code})`
  }
}

export class LifecycleE4ClientError extends Error {
  readonly failure: LifecycleE4Failure

  constructor(failure: LifecycleE4Failure) {
    super(failureMessage(failure))
    this.name = "LifecycleE4ClientError"
    this.failure = failure
  }
}

export interface LifecycleEngineBinding {
  readonly endpoint: string
  readonly engineInstanceId: string
  readonly engineBootId: string
  readonly launchId: string
  readonly protocolVersion: typeof ENGINE_PROTOCOL_VERSION
  readonly sessionContractId: typeof P30_SESSION_CONTRACT_ID
  readonly sessionSchemaSha256: typeof P30_SESSION_SCHEMA_SHA256
}

export interface LifecycleRequestOptions {
  readonly signal?: AbortSignal
}

export interface OwnerCredentialInput extends LifecycleRequestOptions {
  readonly ownerCredential: string
}

export type AcquireOwnerInput =
  | (OwnerCredentialInput & {
      readonly expectedOwnerGeneration: 0
      readonly bootstrapCredential: string
    })
  | (OwnerCredentialInput & {
      readonly expectedOwnerGeneration: number
      readonly bootstrapCredential?: never
    })

export interface OwnerLeaseInput extends OwnerCredentialInput {
  readonly ownerGeneration: number
}

export interface RegisterClientInput extends LifecycleRequestOptions {
  readonly clientInstanceId: string
  readonly workspaceId: string
  readonly lifecycleMode: LifecycleMode
  readonly registrationCredential: string
}

export interface ClientLeaseInput extends LifecycleRequestOptions {
  readonly registrationId: string
  readonly registrationGeneration: number
  readonly clientInstanceId: string
  readonly registrationCredential: string
}

export interface BeginControlDrainInput extends LifecycleRequestOptions {
  readonly ownerGeneration: number
  readonly ownerCredential: string
  readonly registrationId: string
  readonly requesterRegistrationGeneration: number
  readonly requesterClientInstanceId: string
  readonly registrationCredential: string
  readonly expectedAdmissionEpoch: number
}

export interface GracefulControlInput extends OwnerCredentialInput {
  readonly ownerGeneration: number
  readonly drainGeneration: number
  readonly outcome: GracefulControlOutcome
}

export interface RollbackDrainInput extends OwnerCredentialInput {
  readonly ownerGeneration: number
  readonly drainGeneration: number
}

export interface OwnerLeaseResponseBase {
  readonly schemaVersion: typeof ENGINE_OWNER_SCHEMA_VERSION
  readonly engineInstanceId: string
  readonly engineBootId: string
  readonly launchId: string
  readonly ownerGeneration: number
  readonly leaseTtlSeconds: typeof OWNER_LEASE_TTL_SECONDS
  readonly renewalIntervalSeconds: typeof OWNER_RENEWAL_INTERVAL_SECONDS
}

export type OwnerLeaseResponse =
  | (OwnerLeaseResponseBase & {
      readonly result: "acquired" | "renewed"
      readonly expiresAtUnix: number
    })
  | (OwnerLeaseResponseBase & {
      readonly result: "released" | "already_released"
      readonly expiresAtUnix: null
    })

export interface ClientRegistrationResponseBase {
  readonly schemaVersion: typeof ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION
  readonly engineInstanceId: string
  readonly registrationId: string
  readonly registrationGeneration: number
  readonly clientInstanceId: string
  readonly workspaceId: string
  readonly lifecycleMode: LifecycleMode
  readonly firstSliceContractId: typeof P30_SESSION_CONTRACT_ID
  readonly firstSliceSchemaSha256: typeof P30_SESSION_SCHEMA_SHA256
  readonly registeredAtUnix: number
  readonly admissionEpoch: number
  readonly leaseTtlSeconds: typeof OWNER_LEASE_TTL_SECONDS
  readonly renewalIntervalSeconds: typeof OWNER_RENEWAL_INTERVAL_SECONDS
}

export type ClientRegistrationResponse =
  | (ClientRegistrationResponseBase & {
      readonly result: "registered" | "renewed"
      readonly expiresAtUnix: number
    })
  | (ClientRegistrationResponseBase & {
      readonly result: "detached" | "already_detached"
      readonly expiresAtUnix: null
    })

export interface DrainControlResponseBase {
  readonly schemaVersion: typeof ENGINE_DRAIN_CONTROL_SCHEMA_VERSION
  readonly engineInstanceId: string
  readonly engineBootId: string
  readonly launchId: string
  readonly drainGeneration: number
  readonly admissionEpoch: number
}

export type DrainControlResponse =
  | (DrainControlResponseBase & {
      readonly result: "rolled_back"
      readonly sessionAdmissionOpen: true
      readonly turnAdmissionOpen: true
      readonly registrationsOpen: true
      readonly signalPermitted: false
    })
  | (DrainControlResponseBase & {
      readonly result: "hard_signal_decision_pending"
      readonly sessionAdmissionOpen: false
      readonly turnAdmissionOpen: false
      readonly registrationsOpen: false
      readonly signalPermitted: true
    })
  | (DrainControlResponseBase & {
      readonly result:
        | "draining"
        | "shutdown_started"
        | "rollback_permitted"
        | "signal_sent"
        | "process_exited"
      readonly sessionAdmissionOpen: false
      readonly turnAdmissionOpen: false
      readonly registrationsOpen: false
      readonly signalPermitted: false
    })

export interface LifecycleE4Client {
  handshake(request?: LifecycleRequestOptions): Promise<BoundLifecycleE4Client>
}

export interface BoundLifecycleE4Client {
  readonly binding: LifecycleEngineBinding
  acquireOwner(input: AcquireOwnerInput): Promise<OwnerLeaseResponse & { readonly result: "acquired" }>
  renewOwner(input: OwnerLeaseInput): Promise<OwnerLeaseResponse & { readonly result: "renewed" }>
  releaseOwner(input: OwnerLeaseInput): Promise<OwnerLeaseResponse & { readonly result: "released" | "already_released" }>
  registerClient(input: RegisterClientInput): Promise<ClientRegistrationResponse & { readonly result: "registered" }>
  renewClient(input: ClientLeaseInput): Promise<ClientRegistrationResponse & { readonly result: "renewed" }>
  detachClient(input: ClientLeaseInput): Promise<ClientRegistrationResponse & { readonly result: "detached" | "already_detached" }>
  beginControlDrain(input: BeginControlDrainInput): Promise<DrainControlResponse & { readonly result: "draining" }>
  recordGracefulControl(input: GracefulControlInput): Promise<DrainControlResponse & {
    readonly result: "shutdown_started" | "rollback_permitted" | "hard_signal_decision_pending"
  }>
  rollbackDrain(input: RollbackDrainInput): Promise<DrainControlResponse & { readonly result: "rolled_back" }>
}

type RawObject = { readonly [key: string]: unknown }

type BoundAuthResolver = () => Promise<string | undefined>

interface RequestContext {
  readonly fetch: typeof fetch
  readonly endpoint: string
  readonly timeoutMs: number
  readonly resolveBearerToken: BoundAuthResolver
}

interface SafeErrorEnvelope {
  readonly classificationCode: LifecycleClassificationCode | null
  readonly safeCode: string | null
  readonly correlation: LifecycleFailureCorrelation
}

interface JsonResponse {
  readonly value: unknown
  readonly status: number
  readonly correlation: LifecycleFailureCorrelation
}

const isRawObject = (value: unknown): value is RawObject =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const own = (value: RawObject, key: string): unknown =>
  Object.prototype.hasOwnProperty.call(value, key) ? value[key] : undefined

const protocolError = (code: string): never => {
  throw new LifecycleE4ClientError({ kind: "protocol", code })
}

const reflectsSensitiveValue = (candidate: string, secret: string): boolean =>
  secret.length > 0 && candidate.includes(secret)

const LIFECYCLE_CLASSIFICATION_CODES = {
  admission_epoch_conflict: true,
  bootstrap_consumed: true,
  bootstrap_invalid: true,
  bootstrap_rotation_invalid: true,
  bootstrap_unavailable: true,
  drain_clients_active: true,
  drain_conflict: true,
  drain_in_progress: true,
  drain_recovery_failed: true,
  drain_turn_active: true,
  engine_identity_mismatch: true,
  owner_conflict: true,
  owner_expired: true,
  owner_generation_conflict: true,
  owner_identity_mismatch: true,
  registration_conflict: true,
  registration_expired: true,
  registration_generation_conflict: true,
  registration_identity_mismatch: true,
} as const

type LifecycleClassificationCode = keyof typeof LIFECYCLE_CLASSIFICATION_CODES

const safeCorrelation = (
  response: Response,
  sensitiveValues: readonly string[],
): LifecycleFailureCorrelation => {
  const safeValue = (name: string): string | undefined => {
    const value = response.headers.get(name)
    return value !== null
      && value.length <= 128
      && /^[\x21-\x7e]+$/.test(value)
      && sensitiveValues.every((secret) => !reflectsSensitiveValue(value, secret))
      ? value
      : undefined
  }
  const requestId = safeValue("x-request-id")
  const correlationId = safeValue("x-correlation-id")
  return Object.freeze({
    ...(requestId === undefined ? {} : { requestId }),
    ...(correlationId === undefined ? {} : { correlationId }),
  })
}

const parseSafeErrorEnvelope = async (
  response: Response,
  sensitiveValues: readonly string[],
  signal: AbortSignal,
): Promise<SafeErrorEnvelope> => {
  const correlation = safeCorrelation(response, sensitiveValues)
  let value: unknown
  try {
    value = await abortable(response.json(), signal)
  } catch (error) {
    if (isAbortError(error) || signal.aborted) throw error
    return { classificationCode: null, safeCode: null, correlation }
  }
  if (!isRawObject(value)) {
    return { classificationCode: null, safeCode: null, correlation }
  }
  const rawCode = own(value, "error")
  const stableCode = typeof rawCode === "string" && /^[a-z0-9_.-]{1,128}$/.test(rawCode)
    ? rawCode
    : null
  const classificationCode = stableCode !== null
    && Object.prototype.hasOwnProperty.call(LIFECYCLE_CLASSIFICATION_CODES, stableCode)
    ? stableCode as LifecycleClassificationCode
    : null
  const safeCode = stableCode !== null
    && sensitiveValues.every((secret) => !reflectsSensitiveValue(stableCode, secret))
    ? stableCode
    : null
  return { classificationCode, safeCode, correlation }
}

const evidence = (
  status: number,
  code: string | null,
  correlation: LifecycleFailureCorrelation = Object.freeze({}),
): LifecycleHttpFailureEvidence => ({ status, code, correlation, body: REDACTED_BODY })

const localEvidence = (code: string): LifecycleHttpFailureEvidence => evidence(0, code)

const classifyHttpFailure = (
  status: number,
  classificationCode: LifecycleClassificationCode | null,
  safeCode: string | null,
  correlation: LifecycleFailureCorrelation,
): LifecycleE4Failure => {
  const safe = evidence(status, safeCode, correlation)
  if (status === 401 || status === 403) return { kind: "auth", ...safe }
  if (classificationCode === "engine_identity_mismatch") return { kind: "identity-changed", ...safe }
  if (classificationCode === "owner_expired") return { kind: "owner-expired", ...safe }
  if (classificationCode === "owner_conflict" || classificationCode === "owner_generation_conflict") {
    return { kind: "owner-conflict", ...safe }
  }
  if (classificationCode === "registration_expired") return { kind: "registration-expired", ...safe }
  if (classificationCode === "registration_conflict" || classificationCode === "registration_generation_conflict") {
    return { kind: "registration-conflict", ...safe }
  }
  if (classificationCode === "drain_recovery_failed") return { kind: "recovery-failed", ...safe }
  if (
    classificationCode === "drain_conflict"
    || classificationCode === "drain_in_progress"
    || classificationCode === "drain_clients_active"
    || classificationCode === "drain_turn_active"
    || classificationCode === "admission_epoch_conflict"
  ) {
    return { kind: "drain-conflict", ...safe }
  }
  if (
    classificationCode === "bootstrap_invalid"
    || classificationCode === "bootstrap_consumed"
    || classificationCode === "bootstrap_unavailable"
    || classificationCode === "bootstrap_rotation_invalid"
    || classificationCode === "owner_identity_mismatch"
    || classificationCode === "registration_identity_mismatch"
  ) {
    return { kind: "auth", ...safe }
  }
  return { kind: "http", ...safe }
}

const isAbortError = (error: unknown): boolean =>
  isRawObject(error) && own(error, "name") === "AbortError"

const TLS_ERROR_CODES: Readonly<Record<string, true>> = {
  CERT_HAS_EXPIRED: true,
  CERT_NOT_YET_VALID: true,
  CERT_REVOKED: true,
  DEPTH_ZERO_SELF_SIGNED_CERT: true,
  EPROTO: true,
  ERR_SSL_WRONG_VERSION_NUMBER: true,
  ERR_TLS_CERT_ALTNAME_INVALID: true,
  ERR_TLS_CERT_SIGNATURE_ALGORITHM_UNSUPPORTED: true,
  ERR_TLS_CERT_SIGNATURE_ALGORITHM_WEAK: true,
  ERR_TLS_HANDSHAKE_TIMEOUT: true,
  ERR_TLS_INVALID_PROTOCOL_VERSION: true,
  ERR_TLS_PINNED_KEY_NOT_IN_CERT_CHAIN: true,
  ERR_TLS_PROTOCOL_VERSION_CONFLICT: true,
  SELF_SIGNED_CERT_IN_CHAIN: true,
  UNABLE_TO_GET_ISSUER_CERT: true,
  UNABLE_TO_GET_ISSUER_CERT_LOCALLY: true,
  UNABLE_TO_VERIFY_LEAF_SIGNATURE: true,
}

const isTlsError = (error: unknown, endpoint: string): boolean => {
  if (!endpoint.startsWith("https://")) return false
  let current: unknown = error
  for (let depth = 0; depth < 4 && isRawObject(current); depth += 1) {
    const code = own(current, "code")
    if (
      typeof code === "string"
      && (
        Object.prototype.hasOwnProperty.call(TLS_ERROR_CODES, code)
        || code.startsWith("ERR_SSL_")
        || code.startsWith("ERR_TLS_")
      )
    ) {
      return true
    }
    current = own(current, "cause")
  }
  return false
}

const isLoopbackHostname = (hostname: string): boolean => {
  const normalized = hostname.toLowerCase().replace(/\.$/, "")
  if (normalized === "localhost" || normalized === "[::1]" || normalized === "::1") return true
  const octets = normalized.split(".")
  return octets.length === 4
    && octets[0] === "127"
    && octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255)
}

const normalizeEndpoint = (baseUrl: string): string => {
  let url: URL
  try {
    url = new URL(baseUrl)
  } catch {
    return protocolError("invalid_base_url")
  }
  if (url.username || url.password) protocolError("base_url_userinfo_forbidden")
  if (url.search) protocolError("base_url_query_forbidden")
  if (url.hash) protocolError("base_url_fragment_forbidden")
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    protocolError("base_url_protocol_unsupported")
  }
  if (url.protocol === "http:" && !isLoopbackHostname(url.hostname)) {
    protocolError("base_url_https_required")
  }
  const pathPrefix = url.pathname === "/" ? "" : url.pathname.replace(/\/+$/, "")
  return `${url.origin}${pathPrefix}`
}

const buildUrl = (context: RequestContext, route: string): URL =>
  new URL(`${context.endpoint}${route}`)

const validateBearerToken = (value: unknown): string | undefined => {
  if (value === undefined) return undefined
  if (
    typeof value === "string"
    && value.length >= 16
    && value.length <= 8192
    && !/[\s\u0000-\u001f\u007f]/u.test(value)
  ) {
    return value
  }
  throw new LifecycleE4ClientError({ kind: "auth", ...localEvidence("bearer_token_invalid") })
}

const abortable = async <T>(promise: Promise<T>, signal: AbortSignal): Promise<T> => {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError")
  let onAbort: (() => void) | undefined
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(new DOMException("Aborted", "AbortError"))
    signal.addEventListener("abort", onAbort, { once: true })
  })
  try {
    return await Promise.race([promise, aborted])
  } finally {
    if (onAbort !== undefined) signal.removeEventListener("abort", onAbort)
  }
}

const containsSensitiveValue = (
  value: unknown,
  sensitiveValues: readonly string[],
): boolean => {
  if (typeof value === "string") {
    return sensitiveValues.some((secret) => reflectsSensitiveValue(value, secret))
  }
  if (Array.isArray(value)) {
    return value.some((item) => containsSensitiveValue(item, sensitiveValues))
  }
  if (isRawObject(value)) {
    return Object.values(value).some((item) => containsSensitiveValue(item, sensitiveValues))
  }
  return false
}

const requestJson = async (
  context: RequestContext,
  route: string,
  method: "GET" | "POST",
  body: RawObject | undefined,
  credentialHeaders: Readonly<Record<string, string>>,
  callerSignal?: AbortSignal,
): Promise<JsonResponse> => {
  if (callerSignal?.aborted) throw new LifecycleE4ClientError({ kind: "caller-abort" })
  const controller = new AbortController()
  let abortSource: "caller" | "timeout" | null = null
  const onCallerAbort = () => {
    if (abortSource === null) abortSource = "caller"
    controller.abort()
  }
  callerSignal?.addEventListener("abort", onCallerAbort, { once: true })
  const timeout = setTimeout(() => {
    if (abortSource === null) abortSource = "timeout"
    controller.abort()
  }, context.timeoutMs)

  try {
    let resolvedBearerToken: unknown
    try {
      resolvedBearerToken = await abortable(context.resolveBearerToken(), controller.signal)
    } catch (error) {
      if (controller.signal.aborted) throw error
      throw new LifecycleE4ClientError({ kind: "auth", ...localEvidence("bearer_resolution_failed") })
    }
    const bearerToken = validateBearerToken(resolvedBearerToken)

    const sensitiveValues = [
      ...Object.values(credentialHeaders),
      ...(bearerToken === undefined ? [] : [bearerToken]),
    ]
    const response = await abortable(context.fetch(buildUrl(context, route), {
      method,
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(bearerToken === undefined ? {} : { Authorization: `Bearer ${bearerToken}` }),
        ...credentialHeaders,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      redirect: "manual",
    }), controller.signal)

    if ((response.status >= 300 && response.status < 400) || response.redirected) {
      throw new LifecycleE4ClientError({
        kind: "redirect",
        ...evidence(response.status, null, safeCorrelation(response, sensitiveValues)),
      })
    }
    if (!response.ok) {
      const safe = await parseSafeErrorEnvelope(response, sensitiveValues, controller.signal)
      throw new LifecycleE4ClientError(classifyHttpFailure(
        response.status,
        safe.classificationCode,
        safe.safeCode,
        safe.correlation,
      ))
    }
    const correlation = safeCorrelation(response, sensitiveValues)
    let value: unknown
    try {
      value = await abortable(response.json(), controller.signal)
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) throw error
      throw new LifecycleE4ClientError({
        kind: "protocol",
        code: "invalid_json_response",
        status: response.status,
        correlation,
        body: REDACTED_BODY,
      })
    }
    if (containsSensitiveValue(value, sensitiveValues)) {
      throw new LifecycleE4ClientError({
        kind: "protocol",
        code: "sensitive_response_reflection",
        status: response.status,
        correlation,
        body: REDACTED_BODY,
      })
    }
    return { value, status: response.status, correlation }
  } catch (error) {
    if (error instanceof LifecycleE4ClientError) throw error
    if (isAbortError(error) || controller.signal.aborted) {
      if (abortSource === "caller" || callerSignal?.aborted) {
        throw new LifecycleE4ClientError({ kind: "caller-abort" })
      }
      if (abortSource === "timeout") throw new LifecycleE4ClientError({ kind: "timeout" })
    }
    if (isTlsError(error, context.endpoint)) {
      throw new LifecycleE4ClientError({ kind: "tls", code: "tls_transport_error" })
    }
    throw new LifecycleE4ClientError({ kind: "http", ...evidence(0, null) })
  } finally {
    clearTimeout(timeout)
    callerSignal?.removeEventListener("abort", onCallerAbort)
  }
}

const withResponseEvidence = (
  failure: LifecycleE4Failure,
  response: JsonResponse,
): LifecycleE4Failure => {
  const safe = evidence(response.status, "code" in failure ? failure.code : null, response.correlation)
  switch (failure.kind) {
    case "identity-changed": return { kind: "identity-changed", ...safe }
    case "session-schema-mismatch": return { kind: "session-schema-mismatch", ...safe }
    case "registration-schema-mismatch": return { kind: "registration-schema-mismatch", ...safe }
    case "control-schema-mismatch": return { kind: "control-schema-mismatch", ...safe }
    case "protocol":
      return {
        kind: "protocol",
        code: failure.code,
        status: response.status,
        correlation: response.correlation,
        body: REDACTED_BODY,
      }
    default: return failure
  }
}

const decodeResponse = <T>(
  response: JsonResponse,
  decode: (value: unknown) => T,
): T => {
  try {
    return decode(response.value)
  } catch (error) {
    if (error instanceof LifecycleE4ClientError) {
      throw new LifecycleE4ClientError(withResponseEvidence(error.failure, response))
    }
    throw new LifecycleE4ClientError({
      kind: "protocol",
      code: "response_decode_failed",
      status: response.status,
      correlation: response.correlation,
      body: REDACTED_BODY,
    })
  }
}

const expectObject = (value: unknown, code: string): RawObject => {
  if (isRawObject(value)) return value
  return protocolError(code)
}

const expectExactKeys = (value: RawObject, keys: readonly string[], code: string): void => {
  const actual = Object.keys(value)
  if (actual.length !== keys.length || keys.some((key) => !Object.prototype.hasOwnProperty.call(value, key))) {
    protocolError(code)
  }
}

const expectString = (value: unknown, code: string): string => {
  if (typeof value === "string") return value
  return protocolError(code)
}

const expectPattern = (value: unknown, pattern: RegExp, code: string): string => {
  const text = expectString(value, code)
  if (!pattern.test(text)) protocolError(code)
  return text
}

const expectEnum = <T extends string>(value: unknown, allowed: readonly T[], code: string): T => {
  if (typeof value !== "string" || !allowed.includes(value as T)) protocolError(code)
  return value as T
}

const expectNumber = (value: unknown, minimum: number, code: string): number => {
  if (typeof value === "number" && Number.isFinite(value) && value >= minimum) return value
  return protocolError(code)
}

const expectInteger = (value: unknown, minimum: number, code: string): number => {
  const number = expectNumber(value, minimum, code)
  if (Number.isSafeInteger(number)) return number
  return protocolError(code)
}

const expectBoolean = (value: unknown, code: string): boolean => {
  if (typeof value === "boolean") return value
  return protocolError(code)
}

const expectNullableNumber = (value: unknown, code: string): number | null =>
  value === null ? null : expectNumber(value, 0, code)

const AUTHORITY_ID_PATTERN = /^[A-Za-z0-9_-]{43}$/
const CLIENT_ID_PATTERN = /^[A-Za-z0-9_.:-]{16,128}$/
const WORKSPACE_ID_PATTERN = /^workspace:v1:sha256:[0-9a-f]{64}$/
const SCHEMA_DIGEST_PATTERN = /^sha256:[0-9a-f]{64}$/

const RFC3339_DATETIME_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/

const isRfc3339DateTime = (value: string): boolean => {
  const match = RFC3339_DATETIME_PATTERN.exec(value)
  if (match === null) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  const offsetHour = match[7] === undefined ? 0 : Number(match[7])
  const offsetMinute = match[8] === undefined ? 0 : Number(match[8])
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = [
    31,
    leapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ]
  return year >= 1
    && month >= 1
    && month <= 12
    && day >= 1
    && day <= daysInMonth[month - 1]
    && hour <= 23
    && minute <= 59
    && second <= 59
    && offsetHour <= 23
    && offsetMinute <= 59
    && Number.isFinite(Date.parse(value))
}

const validateCredential = (value: string, code: string): string => {
  if (typeof value === "string" && /^[A-Za-z0-9_-]{32,256}$/.test(value)) return value
  throw new LifecycleE4ClientError({ kind: "auth", ...localEvidence(code) })
}

const validatePositiveInteger = (value: number, code: string): number =>
  expectInteger(value, 1, code)

const validateNonnegativeInteger = (value: number, code: string): number =>
  expectInteger(value, 0, code)

const validateAuthorityId = (value: string, code: string): string =>
  expectPattern(value, AUTHORITY_ID_PATTERN, code)

const validateClientId = (value: string, code: string): string =>
  expectPattern(value, CLIENT_ID_PATTERN, code)

const validateWorkspaceId = (value: string, code: string): string =>
  expectPattern(value, WORKSPACE_ID_PATTERN, code)

const schemaFailure = (
  kind: "session-schema-mismatch" | "registration-schema-mismatch" | "control-schema-mismatch",
  code: string,
): never => {
  throw new LifecycleE4ClientError({ kind, ...localEvidence(code) })
}

const identityFailure = (code: string): never => {
  throw new LifecycleE4ClientError({ kind: "identity-changed", ...localEvidence(code) })
}

const decodeHandshake = (
  value: unknown,
  endpoint: string,
  expected: LifecycleSessionContractExpectation,
): LifecycleEngineBinding => {
  const root = expectObject(value, "identity_response_not_object")
  expectExactKeys(root, [
    "schema_version",
    "liveness",
    "process",
    "launch",
    "artifact_revision",
    "protocol",
    "session_contract",
    "session_readiness",
  ], "identity_response_shape_mismatch")
  if (own(root, "schema_version") !== ENGINE_IDENTITY_SCHEMA_VERSION) {
    protocolError("identity_schema_mismatch")
  }

  const liveness = expectObject(own(root, "liveness"), "identity_liveness_invalid")
  expectExactKeys(liveness, ["status"], "identity_liveness_shape_mismatch")
  if (own(liveness, "status") !== "live") protocolError("engine_not_live")

  const process = expectObject(own(root, "process"), "identity_process_invalid")
  expectExactKeys(process, [
    "engine_instance_id",
    "engine_boot_id",
    "started_at",
    "started_at_unix",
    "pid",
  ], "identity_process_shape_mismatch")
  const engineInstanceId = validateAuthorityId(
    expectString(own(process, "engine_instance_id"), "engine_instance_id_invalid"),
    "engine_instance_id_invalid",
  )
  const engineBootId = validateAuthorityId(
    expectString(own(process, "engine_boot_id"), "engine_boot_id_invalid"),
    "engine_boot_id_invalid",
  )
  const startedAt = expectString(own(process, "started_at"), "engine_started_at_invalid")
  if (!isRfc3339DateTime(startedAt)) protocolError("engine_started_at_invalid")
  expectNumber(own(process, "started_at_unix"), 0, "engine_started_at_unix_invalid")
  expectInteger(own(process, "pid"), 1, "engine_process_identity_invalid")

  const launch = expectObject(own(root, "launch"), "identity_launch_invalid")
  expectExactKeys(launch, ["launch_id", "source"], "identity_launch_shape_mismatch")
  const launchId = validateAuthorityId(
    expectString(own(launch, "launch_id"), "launch_id_invalid"),
    "launch_id_invalid",
  )
  expectEnum(own(launch, "source"), ["supervisor", "external_unmanaged"] as const, "launch_source_invalid")

  const artifact = expectObject(own(root, "artifact_revision"), "identity_artifact_invalid")
  expectExactKeys(artifact, [
    "engine_artifact_sha256",
    "served_backend_commit",
    "served_backend_dirty",
  ], "identity_artifact_shape_mismatch")
  expectPattern(own(artifact, "engine_artifact_sha256"), SCHEMA_DIGEST_PATTERN, "engine_artifact_invalid")
  const servedCommit = own(artifact, "served_backend_commit")
  if (servedCommit !== null) expectPattern(servedCommit, /^[0-9a-f]{40}$/, "served_backend_commit_invalid")
  const servedDirty = own(artifact, "served_backend_dirty")
  if (servedDirty !== null) expectBoolean(servedDirty, "served_backend_dirty_invalid")

  const protocol = expectObject(own(root, "protocol"), "identity_protocol_invalid")
  expectExactKeys(protocol, ["protocol_version"], "identity_protocol_shape_mismatch")
  if (own(protocol, "protocol_version") !== ENGINE_PROTOCOL_VERSION) protocolError("protocol_version_mismatch")

  const sessionContract = expectObject(own(root, "session_contract"), "session_contract_invalid")
  expectExactKeys(sessionContract, [
    "contract_id",
    "schema_sha256",
    "compatibility",
  ], "session_contract_shape_mismatch")
  const contractId = expectString(own(sessionContract, "contract_id"), "session_contract_id_invalid")
  const schemaSha256 = expectPattern(own(sessionContract, "schema_sha256"), SCHEMA_DIGEST_PATTERN, "session_schema_digest_invalid")
  const compatibility = expectEnum(
    own(sessionContract, "compatibility"),
    ["compatible", "incompatible"] as const,
    "session_compatibility_invalid",
  )

  const readiness = expectObject(own(root, "session_readiness"), "session_readiness_invalid")
  expectExactKeys(readiness, ["ready", "reason"], "session_readiness_shape_mismatch")
  const ready = expectBoolean(own(readiness, "ready"), "session_ready_invalid")
  const reason = expectEnum(
    own(readiness, "reason"),
    ["ready", "session_contract_missing", "session_contract_mismatch"] as const,
    "session_readiness_reason_invalid",
  )
  if (ready !== (reason === "ready") || ready !== (compatibility === "compatible")) {
    protocolError("session_readiness_contradiction")
  }
  if (contractId !== expected.contractId || schemaSha256 !== expected.schemaSha256) {
    schemaFailure("session-schema-mismatch", "session_contract_echo_mismatch")
  }
  if (!ready) schemaFailure("session-schema-mismatch", reason)

  return Object.freeze({
    endpoint,
    engineInstanceId,
    engineBootId,
    launchId,
    protocolVersion: ENGINE_PROTOCOL_VERSION,
    sessionContractId: P30_SESSION_CONTRACT_ID,
    sessionSchemaSha256: P30_SESSION_SCHEMA_SHA256,
  })
}

const assertAuthorityBinding = (value: RawObject, binding: LifecycleEngineBinding): void => {
  const engineInstanceId = validateAuthorityId(
    expectString(own(value, "engine_instance_id"), "response_engine_instance_id_invalid"),
    "response_engine_instance_id_invalid",
  )
  const engineBootId = validateAuthorityId(
    expectString(own(value, "engine_boot_id"), "response_engine_boot_id_invalid"),
    "response_engine_boot_id_invalid",
  )
  const launchId = validateAuthorityId(
    expectString(own(value, "launch_id"), "response_launch_id_invalid"),
    "response_launch_id_invalid",
  )
  if (
    engineInstanceId !== binding.engineInstanceId
    || engineBootId !== binding.engineBootId
    || launchId !== binding.launchId
  ) {
    identityFailure("authority_binding_echo_mismatch")
  }
}

const decodeOwnerResponse = (
  value: unknown,
  binding: LifecycleEngineBinding,
): OwnerLeaseResponse => {
  const root = expectObject(value, "owner_response_not_object")
  expectExactKeys(root, [
    "schema_version",
    "result",
    "engine_instance_id",
    "engine_boot_id",
    "launch_id",
    "owner_generation",
    "expires_at_unix",
    "lease_ttl_seconds",
    "renewal_interval_seconds",
  ], "owner_response_shape_mismatch")
  if (own(root, "schema_version") !== ENGINE_OWNER_SCHEMA_VERSION) {
    protocolError("owner_schema_mismatch")
  }
  assertAuthorityBinding(root, binding)
  const result = expectEnum(
    own(root, "result"),
    ["acquired", "renewed", "released", "already_released"] as const,
    "owner_result_invalid",
  )
  const ownerGeneration = expectInteger(own(root, "owner_generation"), 1, "owner_generation_invalid")
  const expiresAtUnix = expectNullableNumber(own(root, "expires_at_unix"), "owner_expiry_invalid")
  if ((result === "acquired" || result === "renewed") !== (expiresAtUnix !== null)) {
    protocolError("owner_result_expiry_contradiction")
  }
  if (own(root, "lease_ttl_seconds") !== OWNER_LEASE_TTL_SECONDS) protocolError("owner_lease_ttl_mismatch")
  if (own(root, "renewal_interval_seconds") !== OWNER_RENEWAL_INTERVAL_SECONDS) {
    protocolError("owner_renewal_interval_mismatch")
  }
  const common = {
    schemaVersion: ENGINE_OWNER_SCHEMA_VERSION,
    engineInstanceId: binding.engineInstanceId,
    engineBootId: binding.engineBootId,
    launchId: binding.launchId,
    ownerGeneration,
    leaseTtlSeconds: OWNER_LEASE_TTL_SECONDS,
    renewalIntervalSeconds: OWNER_RENEWAL_INTERVAL_SECONDS,
  }
  return expiresAtUnix === null
    ? { ...common, result: result as "released" | "already_released", expiresAtUnix }
    : { ...common, result: result as "acquired" | "renewed", expiresAtUnix }
}

const decodeRegistrationResponse = (
  value: unknown,
  binding: LifecycleEngineBinding,
): ClientRegistrationResponse => {
  const root = expectObject(value, "registration_response_not_object")
  expectExactKeys(root, [
    "schema_version",
    "result",
    "engine_instance_id",
    "registration_id",
    "registration_generation",
    "client_instance_id",
    "workspace_id",
    "lifecycle_mode",
    "first_slice_contract_id",
    "first_slice_schema_sha256",
    "registered_at_unix",
    "expires_at_unix",
    "admission_epoch",
    "lease_ttl_seconds",
    "renewal_interval_seconds",
  ], "registration_response_shape_mismatch")
  if (own(root, "schema_version") !== ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION) {
    schemaFailure("registration-schema-mismatch", "registration_schema_mismatch")
  }
  const engineInstanceId = validateAuthorityId(
    expectString(own(root, "engine_instance_id"), "registration_engine_instance_id_invalid"),
    "registration_engine_instance_id_invalid",
  )
  if (engineInstanceId !== binding.engineInstanceId) identityFailure("registration_identity_echo_mismatch")
  const firstSliceContractId = expectString(
    own(root, "first_slice_contract_id"),
    "registration_contract_id_invalid",
  )
  const firstSliceSchemaSha256 = expectPattern(
    own(root, "first_slice_schema_sha256"),
    SCHEMA_DIGEST_PATTERN,
    "registration_schema_digest_invalid",
  )
  if (
    firstSliceContractId !== binding.sessionContractId
    || firstSliceSchemaSha256 !== binding.sessionSchemaSha256
  ) {
    schemaFailure("session-schema-mismatch", "registration_contract_echo_mismatch")
  }
  const result = expectEnum(
    own(root, "result"),
    ["registered", "renewed", "detached", "already_detached"] as const,
    "registration_result_invalid",
  )
  const expiresAtUnix = expectNullableNumber(own(root, "expires_at_unix"), "registration_expiry_invalid")
  if ((result === "registered" || result === "renewed") !== (expiresAtUnix !== null)) {
    protocolError("registration_result_expiry_contradiction")
  }
  if (own(root, "lease_ttl_seconds") !== OWNER_LEASE_TTL_SECONDS) {
    schemaFailure("registration-schema-mismatch", "registration_lease_ttl_mismatch")
  }
  if (own(root, "renewal_interval_seconds") !== OWNER_RENEWAL_INTERVAL_SECONDS) {
    schemaFailure("registration-schema-mismatch", "registration_renewal_interval_mismatch")
  }
  const common: ClientRegistrationResponseBase = {
    schemaVersion: ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION,
    engineInstanceId,
    registrationId: validateAuthorityId(
      expectString(own(root, "registration_id"), "registration_id_invalid"),
      "registration_id_invalid",
    ),
    registrationGeneration: expectInteger(
      own(root, "registration_generation"),
      1,
      "registration_generation_invalid",
    ),
    clientInstanceId: validateClientId(
      expectString(own(root, "client_instance_id"), "registration_client_instance_id_invalid"),
      "registration_client_instance_id_invalid",
    ),
    workspaceId: validateWorkspaceId(
      expectString(own(root, "workspace_id"), "registration_workspace_id_invalid"),
      "registration_workspace_id_invalid",
    ),
    lifecycleMode: expectEnum(
      own(root, "lifecycle_mode"),
      ["local-owned", "local-external", "remote"] as const,
      "registration_lifecycle_mode_invalid",
    ),
    firstSliceContractId: P30_SESSION_CONTRACT_ID,
    firstSliceSchemaSha256: P30_SESSION_SCHEMA_SHA256,
    registeredAtUnix: expectNumber(own(root, "registered_at_unix"), 0, "registered_at_unix_invalid"),
    admissionEpoch: expectInteger(own(root, "admission_epoch"), 0, "registration_admission_epoch_invalid"),
    leaseTtlSeconds: OWNER_LEASE_TTL_SECONDS,
    renewalIntervalSeconds: OWNER_RENEWAL_INTERVAL_SECONDS,
  }
  return expiresAtUnix === null
    ? { ...common, result: result as "detached" | "already_detached", expiresAtUnix }
    : { ...common, result: result as "registered" | "renewed", expiresAtUnix }
}

const decodeDrainResponse = (
  value: unknown,
  binding: LifecycleEngineBinding,
): DrainControlResponse => {
  const root = expectObject(value, "control_response_not_object")
  expectExactKeys(root, [
    "schema_version",
    "result",
    "engine_instance_id",
    "engine_boot_id",
    "launch_id",
    "drain_generation",
    "admission_epoch",
    "session_admission_open",
    "turn_admission_open",
    "registrations_open",
    "signal_permitted",
  ], "control_response_shape_mismatch")
  if (own(root, "schema_version") !== ENGINE_DRAIN_CONTROL_SCHEMA_VERSION) {
    schemaFailure("control-schema-mismatch", "control_schema_mismatch")
  }
  assertAuthorityBinding(root, binding)
  const result = expectEnum(own(root, "result"), [
    "draining",
    "shutdown_started",
    "rollback_permitted",
    "hard_signal_decision_pending",
    "signal_sent",
    "process_exited",
    "rolled_back",
  ] as const, "control_result_invalid")
  const sessionAdmissionOpen = expectBoolean(
    own(root, "session_admission_open"),
    "session_admission_open_invalid",
  )
  const turnAdmissionOpen = expectBoolean(
    own(root, "turn_admission_open"),
    "turn_admission_open_invalid",
  )
  const registrationsOpen = expectBoolean(
    own(root, "registrations_open"),
    "registrations_open_invalid",
  )
  const signalPermitted = expectBoolean(own(root, "signal_permitted"), "signal_permitted_invalid")
  const expectedOpen = result === "rolled_back"
  if (
    sessionAdmissionOpen !== expectedOpen
    || turnAdmissionOpen !== expectedOpen
    || registrationsOpen !== expectedOpen
  ) {
    protocolError("control_admission_state_contradiction")
  }
  if (signalPermitted !== (result === "hard_signal_decision_pending")) {
    protocolError("control_signal_state_contradiction")
  }
  const common: DrainControlResponseBase = {
    schemaVersion: ENGINE_DRAIN_CONTROL_SCHEMA_VERSION,
    engineInstanceId: binding.engineInstanceId,
    engineBootId: binding.engineBootId,
    launchId: binding.launchId,
    drainGeneration: expectInteger(own(root, "drain_generation"), 1, "drain_generation_invalid"),
    admissionEpoch: expectInteger(own(root, "admission_epoch"), 0, "control_admission_epoch_invalid"),
  }
  if (result === "rolled_back") {
    return {
      ...common,
      result,
      sessionAdmissionOpen: true,
      turnAdmissionOpen: true,
      registrationsOpen: true,
      signalPermitted: false,
    }
  }
  if (result === "hard_signal_decision_pending") {
    return {
      ...common,
      result,
      sessionAdmissionOpen: false,
      turnAdmissionOpen: false,
      registrationsOpen: false,
      signalPermitted: true,
    }
  }
  return {
    ...common,
    result,
    sessionAdmissionOpen: false,
    turnAdmissionOpen: false,
    registrationsOpen: false,
    signalPermitted: false,
  }
}

const bindingBody = (binding: LifecycleEngineBinding): RawObject => ({
  engine_instance_id: binding.engineInstanceId,
  engine_boot_id: binding.engineBootId,
  launch_id: binding.launchId,
})

const ownerHeaders = (credential: string): Readonly<Record<string, string>> => ({
  "X-Breadboard-Owner-Credential": validateCredential(credential, "invalid_owner_credential"),
})

const registrationHeaders = (credential: string): Readonly<Record<string, string>> => ({
  "X-Breadboard-Registration-Credential": validateCredential(
    credential,
    "invalid_registration_credential",
  ),
})

const responseProtocolError = (response: JsonResponse, code: string): never => {
  throw new LifecycleE4ClientError({
    kind: "protocol",
    code,
    status: response.status,
    correlation: response.correlation,
    body: REDACTED_BODY,
  })
}

const expectMethodResult = <T extends { readonly result: string }, R extends T["result"]>(
  response: T,
  allowed: readonly R[],
  code: string,
  source: JsonResponse,
): T & { readonly result: R } => {
  if (!allowed.includes(response.result as R)) responseProtocolError(source, code)
  return response as T & { readonly result: R }
}

const createBoundClient = (
  context: RequestContext,
  binding: LifecycleEngineBinding,
): BoundLifecycleE4Client => Object.freeze({
  binding,
  acquireOwner: async (input: AcquireOwnerInput) => {
    const expectedOwnerGeneration = validateNonnegativeInteger(
      input.expectedOwnerGeneration,
      "expected_owner_generation_invalid",
    )
    if (expectedOwnerGeneration === Number.MAX_SAFE_INTEGER) {
      protocolError("expected_owner_generation_invalid")
    }
    const headers: Record<string, string> = { ...ownerHeaders(input.ownerCredential) }
    if (expectedOwnerGeneration === 0) {
      const bootstrapCredential = "bootstrapCredential" in input
        ? input.bootstrapCredential
        : undefined
      if (bootstrapCredential === undefined) protocolError("bootstrap_credential_required")
      else {
        headers["X-Breadboard-Bootstrap-Credential"] = validateCredential(
          bootstrapCredential,
          "invalid_bootstrap_credential",
        )
      }
    } else if ("bootstrapCredential" in input && input.bootstrapCredential !== undefined) {
      protocolError("bootstrap_credential_forbidden")
    }
    const raw = await requestJson(
      context,
      "/v1/engine/owner/acquire",
      "POST",
      { ...bindingBody(binding), expected_owner_generation: expectedOwnerGeneration },
      headers,
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeOwnerResponse(value, binding)), ["acquired"] as const, "owner_acquire_result_mismatch", raw)
    const expectedGeneration = expectedOwnerGeneration + 1
    if (response.ownerGeneration !== expectedGeneration) responseProtocolError(raw, "owner_generation_echo_mismatch")
    return response
  },
  renewOwner: async (input: OwnerLeaseInput) => {
    const ownerGeneration = validatePositiveInteger(input.ownerGeneration, "owner_generation_invalid")
    const raw = await requestJson(
      context,
      "/v1/engine/owner/renew",
      "POST",
      { ...bindingBody(binding), owner_generation: ownerGeneration },
      ownerHeaders(input.ownerCredential),
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeOwnerResponse(value, binding)), ["renewed"] as const, "owner_renew_result_mismatch", raw)
    if (response.ownerGeneration !== ownerGeneration) responseProtocolError(raw, "owner_generation_echo_mismatch")
    return response
  },
  releaseOwner: async (input: OwnerLeaseInput) => {
    const ownerGeneration = validatePositiveInteger(input.ownerGeneration, "owner_generation_invalid")
    const raw = await requestJson(
      context,
      "/v1/engine/owner/release",
      "POST",
      { ...bindingBody(binding), owner_generation: ownerGeneration },
      ownerHeaders(input.ownerCredential),
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeOwnerResponse(value, binding)), ["released", "already_released"] as const, "owner_release_result_mismatch", raw)
    if (response.ownerGeneration !== ownerGeneration) responseProtocolError(raw, "owner_generation_echo_mismatch")
    return response
  },
  registerClient: async (input: RegisterClientInput) => {
    const clientInstanceId = validateClientId(input.clientInstanceId, "client_instance_id_invalid")
    const workspaceId = validateWorkspaceId(input.workspaceId, "workspace_id_invalid")
    const lifecycleMode = expectEnum(
      input.lifecycleMode,
      ["local-owned", "local-external", "remote"] as const,
      "lifecycle_mode_invalid",
    )
    const raw = await requestJson(
      context,
      "/v1/engine/clients/register",
      "POST",
      {
        engine_instance_id: binding.engineInstanceId,
        client_instance_id: clientInstanceId,
        workspace_id: workspaceId,
        lifecycle_mode: lifecycleMode,
        first_slice_contract_id: binding.sessionContractId,
        first_slice_schema_sha256: binding.sessionSchemaSha256,
      },
      registrationHeaders(input.registrationCredential),
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeRegistrationResponse(value, binding)), ["registered"] as const, "client_register_result_mismatch", raw)
    if (
      response.clientInstanceId !== clientInstanceId
      || response.workspaceId !== workspaceId
      || response.lifecycleMode !== lifecycleMode
    ) {
      responseProtocolError(raw, "registration_request_echo_mismatch")
    }
    return response
  },
  renewClient: async (input: ClientLeaseInput) => {
    const registrationId = validateAuthorityId(input.registrationId, "registration_id_invalid")
    const registrationGeneration = validatePositiveInteger(
      input.registrationGeneration,
      "registration_generation_invalid",
    )
    const clientInstanceId = validateClientId(input.clientInstanceId, "client_instance_id_invalid")
    const raw = await requestJson(
      context,
      "/v1/engine/clients/renew",
      "POST",
      {
        engine_instance_id: binding.engineInstanceId,
        registration_id: registrationId,
        registration_generation: registrationGeneration,
        client_instance_id: clientInstanceId,
      },
      registrationHeaders(input.registrationCredential),
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeRegistrationResponse(value, binding)), ["renewed"] as const, "client_renew_result_mismatch", raw)
    if (
      response.registrationId !== registrationId
      || response.registrationGeneration !== registrationGeneration
      || response.clientInstanceId !== clientInstanceId
    ) {
      responseProtocolError(raw, "registration_request_echo_mismatch")
    }
    return response
  },
  detachClient: async (input: ClientLeaseInput) => {
    const registrationId = validateAuthorityId(input.registrationId, "registration_id_invalid")
    const registrationGeneration = validatePositiveInteger(
      input.registrationGeneration,
      "registration_generation_invalid",
    )
    const clientInstanceId = validateClientId(input.clientInstanceId, "client_instance_id_invalid")
    const raw = await requestJson(
      context,
      "/v1/engine/clients/detach",
      "POST",
      {
        engine_instance_id: binding.engineInstanceId,
        registration_id: registrationId,
        registration_generation: registrationGeneration,
        client_instance_id: clientInstanceId,
      },
      registrationHeaders(input.registrationCredential),
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeRegistrationResponse(value, binding)), ["detached", "already_detached"] as const, "client_detach_result_mismatch", raw)
    if (
      response.registrationId !== registrationId
      || response.registrationGeneration !== registrationGeneration
      || response.clientInstanceId !== clientInstanceId
    ) {
      responseProtocolError(raw, "registration_request_echo_mismatch")
    }
    return response
  },
  beginControlDrain: async (input: BeginControlDrainInput) => {
    const ownerGeneration = validatePositiveInteger(input.ownerGeneration, "owner_generation_invalid")
    const registrationId = validateAuthorityId(input.registrationId, "registration_id_invalid")
    const requesterRegistrationGeneration = validatePositiveInteger(
      input.requesterRegistrationGeneration,
      "requester_registration_generation_invalid",
    )
    const requesterClientInstanceId = validateClientId(
      input.requesterClientInstanceId,
      "requester_client_instance_id_invalid",
    )
    const expectedAdmissionEpoch = validateNonnegativeInteger(
      input.expectedAdmissionEpoch,
      "expected_admission_epoch_invalid",
    )
    if (expectedAdmissionEpoch === Number.MAX_SAFE_INTEGER) {
      protocolError("expected_admission_epoch_invalid")
    }
    const raw = await requestJson(
      context,
      "/v1/engine/control/drain",
      "POST",
      {
        ...bindingBody(binding),
        owner_generation: ownerGeneration,
        registration_id: registrationId,
        requester_registration_generation: requesterRegistrationGeneration,
        requester_client_instance_id: requesterClientInstanceId,
        expected_admission_epoch: expectedAdmissionEpoch,
      },
      {
        ...ownerHeaders(input.ownerCredential),
        ...registrationHeaders(input.registrationCredential),
      },
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeDrainResponse(value, binding)), ["draining"] as const, "control_drain_result_mismatch", raw)
    if (response.admissionEpoch !== expectedAdmissionEpoch + 1) {
      responseProtocolError(raw, "control_admission_epoch_echo_mismatch")
    }
    return response
  },
  recordGracefulControl: async (input: GracefulControlInput) => {
    const ownerGeneration = validatePositiveInteger(input.ownerGeneration, "owner_generation_invalid")
    const drainGeneration = validatePositiveInteger(input.drainGeneration, "drain_generation_invalid")
    const outcome = expectEnum(
      input.outcome,
      ["accepted", "definitive_rejection", "timeout", "uncertain"] as const,
      "graceful_outcome_invalid",
    )
    const raw = await requestJson(
      context,
      "/v1/engine/control/graceful-result",
      "POST",
      {
        ...bindingBody(binding),
        owner_generation: ownerGeneration,
        drain_generation: drainGeneration,
        outcome,
      },
      ownerHeaders(input.ownerCredential),
      input.signal,
    )
    const response = decodeResponse(raw, (value) => decodeDrainResponse(value, binding))
    const expectedResult = outcome === "accepted"
      ? "shutdown_started"
      : outcome === "definitive_rejection"
        ? "rollback_permitted"
        : "hard_signal_decision_pending"
    const narrowed = expectMethodResult(response, [expectedResult] as const, "graceful_control_result_mismatch", raw)
    if (narrowed.drainGeneration !== drainGeneration) responseProtocolError(raw, "drain_generation_echo_mismatch")
    return narrowed
  },
  rollbackDrain: async (input: RollbackDrainInput) => {
    const ownerGeneration = validatePositiveInteger(input.ownerGeneration, "owner_generation_invalid")
    const drainGeneration = validatePositiveInteger(input.drainGeneration, "drain_generation_invalid")
    const raw = await requestJson(
      context,
      "/v1/engine/control/drain-rollback",
      "POST",
      {
        ...bindingBody(binding),
        owner_generation: ownerGeneration,
        drain_generation: drainGeneration,
      },
      ownerHeaders(input.ownerCredential),
      input.signal,
    )
    const response = expectMethodResult(decodeResponse(raw, (value) => decodeDrainResponse(value, binding)), ["rolled_back"] as const, "drain_rollback_result_mismatch", raw)
    if (response.drainGeneration !== drainGeneration) responseProtocolError(raw, "drain_generation_echo_mismatch")
    return response
  },
})

export const createLifecycleE4Client = (config: LifecycleE4ClientConfig): LifecycleE4Client => {
  const endpoint = normalizeEndpoint(config.baseUrl)
  if (
    !isRawObject(config.expectedSessionContract)
    || config.expectedSessionContract.contractId !== P30_SESSION_CONTRACT_ID
    || config.expectedSessionContract.schemaSha256 !== P30_SESSION_SCHEMA_SHA256
  ) {
    schemaFailure("session-schema-mismatch", "expected_session_contract_mismatch")
  }
  if (config.bearerToken !== undefined && config.bearerTokenResolver !== undefined) {
    protocolError("multiple_bearer_sources")
  }
  const timeoutMs = config.timeoutMs ?? 30_000
  if (
    !Number.isSafeInteger(timeoutMs)
    || timeoutMs < 1
    || timeoutMs > 2_147_483_647
  ) {
    protocolError("invalid_timeout_ms")
  }
  const fetchImplementation = config.fetch ?? globalThis.fetch
  if (typeof fetchImplementation !== "function") protocolError("fetch_unavailable")
  const directBearerToken = config.bearerToken
  const configuredResolver = config.bearerTokenResolver
  const resolveBearerToken: BoundAuthResolver = configuredResolver === undefined
    ? async () => directBearerToken
    : async () => configuredResolver()
  const context: RequestContext = Object.freeze({
    fetch: fetchImplementation,
    endpoint,
    timeoutMs,
    resolveBearerToken,
  })
  const expectedSessionContract = Object.freeze({
    contractId: config.expectedSessionContract.contractId,
    schemaSha256: config.expectedSessionContract.schemaSha256,
  })
  return Object.freeze({
    handshake: async (request: LifecycleRequestOptions = {}) => {
      const raw = await requestJson(
        context,
        "/v1/engine/identity",
        "GET",
        undefined,
        {},
        request.signal,
      )
      const binding = decodeResponse(raw, (value) => decodeHandshake(value, endpoint, expectedSessionContract))
      return createBoundClient(context, binding)
    },
  })
}
