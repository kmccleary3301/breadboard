import type { SessionEvent } from "../api/types.js"
import { rememberSession } from "../cache/sessionCache.js"
import { getCliSdk } from "./commandRuntime.js"
import { collectSessionStream } from "./sessionStream.js"

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
  const sdk = getCliSdk()
  const summary = await sdk.api().getSession(options.sessionId)
  await rememberSession(summary)
  const { events, completion } = await collectSessionStream(sdk.stream(options.sessionId, { signal: undefined }), onEvent)
  return { sessionId: options.sessionId, events, completion }
}
