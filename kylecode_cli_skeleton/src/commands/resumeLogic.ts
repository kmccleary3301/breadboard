import type { SessionEvent } from "../api/types.js"
import { rememberSession } from "../cache/sessionCache.js"
import { CliProviders } from "../providers/cliProviders.js"

export interface ResumeOptions {
  readonly sessionId: string
}

export interface ResumeResult {
  readonly sessionId: string
  readonly events: SessionEvent[]
  readonly completion?: unknown
}

export const runResume = async (
  options: ResumeOptions,
  onEvent?: (event: SessionEvent) => Promise<void> | void,
): Promise<ResumeResult> => {
  const sdk = CliProviders.sdk
  const summary = await sdk.api().getSession(options.sessionId)
  await rememberSession(summary)
  const events: SessionEvent[] = []
  let completion: unknown = undefined
  for await (const event of sdk.stream(options.sessionId, { signal: undefined })) {
    events.push(event)
    await onEvent?.(event)
    if (event.type === "completion") {
      completion = event.payload?.summary ?? event.payload
      break
    }
  }
  return { sessionId: options.sessionId, events, completion }
}
