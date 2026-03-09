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
