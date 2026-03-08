import type { KernelEventV1, SessionTranscriptV1Item } from "@breadboard/kernel-contracts"

export function buildKernelEventId(runId: string, seq: number): string {
  return `${runId}:evt:${seq}`
}

export function cloneTranscriptItem<T extends SessionTranscriptV1Item>(item: T): T {
  return JSON.parse(JSON.stringify(item)) as T
}

export function eventBelongsToSession(event: KernelEventV1, sessionId: string): boolean {
  return event.sessionId === sessionId
}
