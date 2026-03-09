import type { SupportClaim } from "@breadboard/backbone"

export type HostKitMode = "supported" | "fallback"

export interface HostKitClassification<Request> {
  readonly mode: HostKitMode
  readonly supportClaim: SupportClaim
  readonly unsupportedFields: readonly string[]
  readonly request: Request
}

export interface HostKitInvocation<Result, Invocation> {
  readonly mode: HostKitMode
  readonly result: Result
  readonly invocation: Invocation
  readonly supportClaim: SupportClaim
}

export interface HostKit<Request, Result, Invocation> {
  readonly id: string
  classify(request: Request): HostKitClassification<Request>
  invoke(request: Request): Promise<HostKitInvocation<Result, Invocation>>
}

export interface HostKitOptions<Request, Result, Invocation> {
  readonly id: string
  readonly classify: (request: Request) => HostKitClassification<Request>
  readonly invoke: (request: Request) => Promise<HostKitInvocation<Result, Invocation>>
}

/**
 * Apply consistent override semantics to a public SupportClaim without mutating the base claim.
 */
export function normalizeHostKitSupportClaim(
  claim: SupportClaim,
  overrides: Partial<SupportClaim> = {},
): SupportClaim {
  return {
    ...claim,
    ...overrides,
    unsupportedFields: [...(overrides.unsupportedFields ?? claim.unsupportedFields)],
  }
}

/**
 * Build a fallback-mode Host Kit invocation while preserving a normalized support claim.
 */
export function buildFallbackHostKitInvocation<Result, Invocation>(options: {
  result: Result
  invocation: Invocation
  supportClaim: SupportClaim
}): HostKitInvocation<Result, Invocation> {
  return {
    mode: "fallback",
    result: options.result,
    invocation: options.invocation,
    supportClaim: normalizeHostKitSupportClaim(options.supportClaim, {
      level: "fallback",
      fallbackAvailable: true,
      confidence: options.supportClaim.confidence === "high" ? "medium" : options.supportClaim.confidence,
    }),
  }
}

/**
 * Build a supported-mode Host Kit invocation while preserving a normalized support claim.
 */
export function buildSupportedHostKitInvocation<Result, Invocation>(options: {
  result: Result
  invocation: Invocation
  supportClaim: SupportClaim
}): HostKitInvocation<Result, Invocation> {
  return {
    mode: "supported",
    result: options.result,
    invocation: options.invocation,
    supportClaim: normalizeHostKitSupportClaim(options.supportClaim, {
      level: "supported",
    }),
  }
}

/**
 * Create the stable Host Kit classify/invoke surface for a concrete host integration.
 */
export function createHostKit<Request, Result, Invocation>(
  options: HostKitOptions<Request, Result, Invocation>,
): HostKit<Request, Result, Invocation> {
  return {
    id: options.id,
    classify(request: Request): HostKitClassification<Request> {
      return options.classify(request)
    },
    invoke(request: Request): Promise<HostKitInvocation<Result, Invocation>> {
      return options.invoke(request)
    },
  }
}
