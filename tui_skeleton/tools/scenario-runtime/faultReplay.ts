import type { FaultEventStep } from "./schema"

export type ReplayStepLike = Record<string, unknown> & {
  readonly delayMs?: number
  readonly event: Record<string, unknown>
}

const faultMessage = (event: FaultEventStep): string => {
  switch (event.kind) {
    case "disconnect":
      return `Synthetic lifecycle disconnect: ${event.reason}`
    case "engine-death":
      return `Synthetic engine death during ${event.phase}${event.signal ? ` (${event.signal})` : ""}`
    case "stream-stall":
      return `Synthetic stream stall on ${event.streamId} after ${event.durationMs}ms`
    case "replay-duplicate":
      return `Synthetic replay duplicate for ${event.eventIds.join(", ") || "unknown event"}`
    case "event-zero-replay":
      return `Synthetic event-zero replay from ${event.from}`
    case "stale-generation":
      return `Synthetic stale generation for ${event.streamId} (${event.staleEvents.length} stale events)`
  }
}

export const faultToReplaySteps = (event: FaultEventStep): ReplayStepLike[] => [
  {
    delayMs: 50,
    event: {
      type: "error",
      turn: 1,
      payload: {
        message: faultMessage(event),
        syntheticFault: true,
        fault: event,
      },
    },
  },
]
