import type { SessionEvent } from "../api/types.js"

export interface SessionStreamResult {
  readonly events: SessionEvent[]
  readonly completion?: unknown
}

export const completionSummary = (event: SessionEvent): unknown => event.payload?.summary ?? event.payload

export const collectSessionStream = async (
  stream: AsyncIterable<SessionEvent>,
  onEvent?: (event: SessionEvent) => Promise<void> | void,
): Promise<SessionStreamResult> => {
  const events: SessionEvent[] = []
  let completion: unknown = undefined
  for await (const event of stream) {
    events.push(event)
    await onEvent?.(event)
    if (event.type === "completion") {
      completion = completionSummary(event)
      break
    }
  }
  return { events, completion }
}
