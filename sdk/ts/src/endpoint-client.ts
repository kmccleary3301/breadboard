import {
  LifecycleE4ClientError,
  createLifecycleE4Client,
  type LifecycleE4Client,
  type LifecycleMode,
  type LifecycleSessionContractExpectation,
} from "./lifecycle-client.js"
import {
  createCanonicalE4Client,
  type CanonicalE4Client,
} from "./session-runtime.js"

export type EndpointAuthReference =
  | { readonly kind: "absent" }
  | { readonly kind: "process-secret"; readonly credentialReference: string }
  | { readonly kind: "keychain-reference"; readonly credentialReference: string }
  | { readonly kind: "mTLS-reference"; readonly credentialReference: string }

export type EndpointTlsPolicy =
  | { readonly kind: "local-loopback" }
  | { readonly kind: "system-trust"; readonly spkiSha256?: string }

export interface EndpointSecurityBinding {
  readonly lifecycleMode: LifecycleMode
  readonly tls: EndpointTlsPolicy
  readonly auth: EndpointAuthReference
}

export interface EndpointTransportBinding extends EndpointSecurityBinding {
  readonly normalizedEndpoint: string
}

export interface EndpointScopedTransport {
  readonly binding: EndpointTransportBinding
  attestBinding(expected: EndpointTransportBinding): true
  request(url: URL, init: RequestInit): Promise<Response>
}

export interface EndpointScopedE4ClientConfig {
  readonly transport: EndpointScopedTransport
  readonly expectedSessionContract: LifecycleSessionContractExpectation
  readonly requestTimeoutMs?: number
}

export interface EndpointScopedE4Client {
  readonly binding: EndpointTransportBinding
  readonly session: CanonicalE4Client
  readonly lifecycle: LifecycleE4Client
}

export interface LocalEndpointScopedTransportConfig {
  readonly baseUrl: string
  readonly lifecycleMode: "local-owned" | "local-external"
  readonly fetch?: typeof fetch
}

const SPKI_SHA256_PATTERN = /^sha256:[0-9a-f]{64}$/
const REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$/

const configurationError = (code: string): never => {
  throw new LifecycleE4ClientError({ kind: "protocol", code })
}

const isLoopbackHostname = (hostname: string): boolean => {
  const normalized = hostname.toLowerCase().replace(/\.$/, "")
  if (normalized === "localhost" || normalized === "127.0.0.1" || normalized === "[::1]") return true
  if (/^127(?:\.\d{1,3}){3}$/.test(normalized)) {
    return normalized.split(".").slice(1).every((part) => Number(part) <= 255)
  }
  return false
}

const normalizeEndpoint = (baseUrl: string): string => {
  let url: URL
  try {
    url = new URL(baseUrl)
  } catch {
    return configurationError("invalid_endpoint")
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") configurationError("invalid_endpoint_scheme")
  if (url.username || url.password || url.search || url.hash) configurationError("unsafe_endpoint")
  url.pathname = url.pathname.replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/"
  return url.toString().replace(/\/$/, "")
}

const freezeAuth = (auth: EndpointAuthReference): EndpointAuthReference => {
  if (auth.kind === "absent") return Object.freeze({ kind: "absent" })
  if (!REFERENCE_PATTERN.test(auth.credentialReference)) configurationError("invalid_credential_reference")
  return Object.freeze({ kind: auth.kind, credentialReference: auth.credentialReference })
}

const freezeTls = (tls: EndpointTlsPolicy): EndpointTlsPolicy => {
  if (tls.kind === "local-loopback") return Object.freeze({ kind: "local-loopback" })
  if (tls.spkiSha256 !== undefined && !SPKI_SHA256_PATTERN.test(tls.spkiSha256)) {
    configurationError("invalid_spki_sha256")
  }
  return Object.freeze({
    kind: "system-trust",
    ...(tls.spkiSha256 === undefined ? {} : { spkiSha256: tls.spkiSha256 }),
  })
}

const validateBinding = (binding: EndpointTransportBinding): EndpointTransportBinding => {
  if (typeof binding !== "object" || binding === null) configurationError("endpoint_binding_required")
  const normalizedEndpoint = normalizeEndpoint(binding.normalizedEndpoint)
  if (normalizedEndpoint !== binding.normalizedEndpoint) configurationError("endpoint_not_normalized")
  const url = new URL(normalizedEndpoint)
  const loopback = isLoopbackHostname(url.hostname)
  const tls = freezeTls(binding.tls)
  const auth = freezeAuth(binding.auth)
  if (binding.lifecycleMode === "remote") {
    if (url.protocol !== "https:" || loopback) configurationError("remote_endpoint_invalid")
    if (tls.kind !== "system-trust") configurationError("remote_tls_binding_required")
    if (auth.kind === "absent") configurationError("remote_auth_reference_required")
  } else if (binding.lifecycleMode === "local-owned" || binding.lifecycleMode === "local-external") {
    if (!loopback) configurationError("local_endpoint_not_loopback")
    if (tls.kind !== "local-loopback") configurationError("local_tls_binding_invalid")
  } else {
    configurationError("lifecycle_mode_invalid")
  }
  return Object.freeze({
    normalizedEndpoint,
    lifecycleMode: binding.lifecycleMode,
    tls,
    auth,
  })
}

const bindingsEqual = (left: EndpointTransportBinding, right: EndpointTransportBinding): boolean =>
  left.normalizedEndpoint === right.normalizedEndpoint
  && left.lifecycleMode === right.lifecycleMode
  && left.tls.kind === right.tls.kind
  && (left.tls.kind !== "system-trust" || right.tls.kind !== "system-trust" || left.tls.spkiSha256 === right.tls.spkiSha256)
  && left.auth.kind === right.auth.kind
  && (left.auth.kind === "absent" || right.auth.kind === "absent" || left.auth.credentialReference === right.auth.credentialReference)

const scopedFetch = (transport: EndpointScopedTransport, binding: EndpointTransportBinding): typeof fetch =>
  async (input, init) => {
    const url = input instanceof URL
      ? input
      : typeof input === "string"
        ? new URL(input)
        : new URL(input.url)
    const endpoint = new URL(binding.normalizedEndpoint)
    const prefix = endpoint.pathname.replace(/\/$/, "")
    if (
      url.origin !== endpoint.origin
      || (prefix !== "" && url.pathname !== prefix && !url.pathname.startsWith(`${prefix}/`))
    ) {
      configurationError("request_outside_endpoint_binding")
    }
    return transport.request(url, init ?? {})
  }

export const createLocalEndpointScopedTransport = (
  config: LocalEndpointScopedTransportConfig,
): EndpointScopedTransport => {
  const binding = validateBinding({
    normalizedEndpoint: normalizeEndpoint(config.baseUrl),
    lifecycleMode: config.lifecycleMode,
    tls: { kind: "local-loopback" },
    auth: { kind: "absent" },
  })
  const fetchImplementation = config.fetch ?? globalThis.fetch
  if (typeof fetchImplementation !== "function") configurationError("fetch_unavailable")
  return Object.freeze({
    binding,
    attestBinding: (expected: EndpointTransportBinding): true => {
      if (!bindingsEqual(binding, expected)) configurationError("transport_binding_mismatch")
      return true
    },
    request: (url: URL, init: RequestInit) => fetchImplementation(url, init),
  })
}

export const createEndpointScopedE4Client = (
  config: EndpointScopedE4ClientConfig,
): EndpointScopedE4Client => {
  if (typeof config !== "object" || config === null || typeof config.transport !== "object" || config.transport === null) {
    configurationError("endpoint_scoped_transport_required")
  }
  const transport = config.transport
  if (
    typeof transport.request !== "function"
    || typeof transport.attestBinding !== "function"
    || !Object.isFrozen(transport)
  ) {
    configurationError("endpoint_scoped_transport_required")
  }
  const binding = validateBinding(transport.binding)
  if (!Object.isFrozen(transport.binding) || !bindingsEqual(transport.binding, binding)) {
    configurationError("transport_binding_mismatch")
  }
  let attested: true
  try {
    attested = transport.attestBinding(binding)
  } catch {
    return configurationError("transport_binding_not_attested")
  }
  if (attested !== true) configurationError("transport_binding_not_attested")
  const fetchImplementation = scopedFetch(transport, binding)
  const timeoutMs = config.requestTimeoutMs
  const lifecycle = createLifecycleE4Client({
    baseUrl: binding.normalizedEndpoint,
    expectedSessionContract: config.expectedSessionContract,
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
    fetch: fetchImplementation,
  })
  const session = createCanonicalE4Client({
    baseUrl: binding.normalizedEndpoint,
    ...(timeoutMs === undefined ? {} : { requestTimeoutMs: timeoutMs }),
    fetch: fetchImplementation,
  })
  return Object.freeze({ binding, session, lifecycle })
}
