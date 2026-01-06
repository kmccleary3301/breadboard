import { promises as fs } from "node:fs"
import { createParser, type ParsedEvent, type ReconnectInterval } from "eventsource-parser"

export interface ContractIssue {
  readonly code: string
  readonly message: string
  readonly eventIndex?: number
  readonly eventId?: string
  readonly eventType?: string
}

export interface ContractReport {
  readonly summary: {
    readonly eventCount: number
    readonly toolCallCount: number
    readonly toolResultCount: number
    readonly untrackedToolCalls: number
    readonly untrackedToolResults: number
    readonly completionSeen: boolean
    readonly runFinishedSeen: boolean
    readonly seqMonotonic: boolean | null
    readonly timestampMonotonic: boolean | null
    readonly warningsCount: number
    readonly errorsCount: number
  }
  readonly errors: ContractIssue[]
  readonly warnings: ContractIssue[]
}

type AnyEvent = Record<string, any>

export interface ContractOptions {
  readonly requireSeq?: boolean
  readonly requireData?: boolean
  readonly requireTimestampMs?: boolean
  readonly requireProtocolVersion?: boolean
}

const requireSeq = () =>
  process.env.BREADBOARD_CONTRACT_REQUIRE_SEQ === "1" || process.env.CONTRACT_REQUIRE_SEQ === "1"
const requireData = () =>
  process.env.BREADBOARD_CONTRACT_REQUIRE_DATA === "1" || process.env.CONTRACT_REQUIRE_DATA === "1"
const requireTimestampMs = () =>
  process.env.BREADBOARD_CONTRACT_REQUIRE_TIMESTAMP_MS === "1" ||
  process.env.CONTRACT_REQUIRE_TIMESTAMP_MS === "1"
const requireProtocolVersion = () =>
  process.env.BREADBOARD_CONTRACT_REQUIRE_PROTOCOL_VERSION === "1" ||
  process.env.CONTRACT_REQUIRE_PROTOCOL_VERSION === "1"

const parseSseEvents = (raw: string): AnyEvent[] => {
  const events: AnyEvent[] = []
  const parser = createParser(((event: ParsedEvent | ReconnectInterval) => {
    if (event.type === "event" && "data" in event && event.data) {
      try {
        const payload = JSON.parse(event.data) as AnyEvent
        events.push(payload)
      } catch {
        // ignore malformed event payloads
      }
    }
  }) as (event: ParsedEvent | ReconnectInterval) => void)
  parser.feed(raw)
  return events
}

const extractCallId = (event: AnyEvent): string | null => {
  const payload = event?.payload ?? event?.data ?? null
  const direct = typeof payload?.call_id === "string" ? payload.call_id : null
  if (direct) return direct
  const toolCallId = typeof payload?.tool?.call_id === "string" ? payload.tool.call_id : null
  if (toolCallId) return toolCallId
  const toolId = typeof payload?.tool?.id === "string" ? payload.tool.id : null
  return toolId ?? null
}

const extractSeq = (event: AnyEvent): number | null => {
  const seq = event?.seq ?? event?.sequence ?? null
  return typeof seq === "number" && Number.isFinite(seq) ? seq : null
}

const extractTimestamp = (event: AnyEvent): number | null => {
  const timestampMs = event?.timestamp_ms
  if (typeof timestampMs === "number" && Number.isFinite(timestampMs)) {
    return timestampMs
  }
  const timestamp = event?.timestamp
  if (typeof timestamp === "number" && Number.isFinite(timestamp)) {
    return timestamp
  }
  return null
}

export const runContractChecks = async (ssePath: string, options?: ContractOptions): Promise<ContractReport> => {
  const raw = await fs.readFile(ssePath, "utf8").catch(() => "")
  const events = raw ? parseSseEvents(raw) : []
  const errors: ContractIssue[] = []
  const warnings: ContractIssue[] = []
  const strictSeq = options?.requireSeq ?? requireSeq()
  const strictData = options?.requireData ?? requireData()
  const strictTimestampMs = options?.requireTimestampMs ?? requireTimestampMs()
  const strictProtocol = options?.requireProtocolVersion ?? requireProtocolVersion()

  if (events.length === 0) {
    warnings.push({
      code: "no-events",
      message: "No SSE events were parsed from sse_events.txt.",
    })
  }

  const toolCallIds = new Map<string, number>()
  const unresolvedToolCalls = new Set<string>()
  let toolCallCount = 0
  let toolResultCount = 0
  let untrackedToolCalls = 0
  let untrackedToolResults = 0
  let completionSeen = false
  let runFinishedSeen = false

  let seqMonotonic: boolean | null = null
  let timestampMonotonic: boolean | null = null
  let lastSeq: number | null = null
  let lastTimestamp: number | null = null
  let lastTurn: number | null = null

  events.forEach((event, index) => {
    const type = typeof event?.type === "string" ? event.type : "unknown"
    const eventId = typeof event?.id === "string" ? event.id : undefined
    const dataValue = event?.data
    if (dataValue == null && strictData) {
      errors.push({
        code: "data-missing",
        message: "Missing data payload; contract requires data field.",
        eventIndex: index,
        eventId,
        eventType: type,
      })
    } else if (dataValue == null) {
      warnings.push({
        code: "data-missing",
        message: "Missing data payload; legacy payload only.",
        eventIndex: index,
        eventId,
        eventType: type,
      })
    }

    const protocolVersion = typeof event?.protocol_version === "string" ? event.protocol_version : null
    if (!protocolVersion && strictProtocol) {
      errors.push({
        code: "protocol-version-missing",
        message: "Missing protocol_version; contract requires protocol_version field.",
        eventIndex: index,
        eventId,
        eventType: type,
      })
    }

    const seq = extractSeq(event)
    if (seq === null && strictSeq) {
      errors.push({
        code: "seq-missing",
        message: "Missing seq in event; contract requires seq for ordering.",
        eventIndex: index,
        eventId,
        eventType: type,
      })
    } else if (seq === null) {
      warnings.push({
        code: "seq-missing",
        message: "Missing seq in event; ordering checks skipped for this entry.",
        eventIndex: index,
        eventId,
        eventType: type,
      })
    }
    if (seq !== null) {
      if (lastSeq !== null && seq < lastSeq) {
        errors.push({
          code: "seq-non-monotonic",
          message: `Sequence decreased from ${lastSeq} to ${seq}.`,
          eventIndex: index,
          eventId,
          eventType: type,
        })
      }
      lastSeq = seq
      if (seqMonotonic === null) seqMonotonic = true
    }

    const timestamp = extractTimestamp(event)
    if (strictTimestampMs) {
      const tsMs = event?.timestamp_ms
      if (!(typeof tsMs === "number" && Number.isFinite(tsMs))) {
        errors.push({
          code: "timestamp-ms-missing",
          message: "Missing timestamp_ms; contract requires milliseconds.",
          eventIndex: index,
          eventId,
          eventType: type,
        })
      }
    }
    if (timestamp !== null) {
      if (lastTimestamp !== null && timestamp < lastTimestamp) {
        warnings.push({
          code: "timestamp-non-monotonic",
          message: `Timestamp decreased from ${lastTimestamp} to ${timestamp}.`,
          eventIndex: index,
          eventId,
          eventType: type,
        })
      }
      lastTimestamp = timestamp
      if (timestampMonotonic === null) timestampMonotonic = true
    }

    const turn = typeof event?.turn === "number" ? event.turn : null
    if (turn !== null) {
      if (lastTurn !== null && turn < lastTurn) {
        warnings.push({
          code: "turn-non-monotonic",
          message: `Turn decreased from ${lastTurn} to ${turn}.`,
          eventIndex: index,
          eventId,
          eventType: type,
        })
      }
      lastTurn = turn
    }

    if (type === "tool_call") {
      toolCallCount += 1
      const callId = extractCallId(event)
      if (!callId) {
        untrackedToolCalls += 1
        warnings.push({
          code: "tool-call-missing-call-id",
          message: "Tool call missing call_id; pairing validation skipped.",
          eventIndex: index,
          eventId,
          eventType: type,
        })
      } else {
        toolCallIds.set(callId, index)
        unresolvedToolCalls.add(callId)
      }
    }

    if (type === "tool_result") {
      toolResultCount += 1
      const callId = extractCallId(event)
      if (!callId) {
        untrackedToolResults += 1
        warnings.push({
          code: "tool-result-missing-call-id",
          message: "Tool result missing call_id; pairing validation skipped.",
          eventIndex: index,
          eventId,
          eventType: type,
        })
      } else if (!toolCallIds.has(callId)) {
        errors.push({
          code: "tool-result-without-call",
          message: `Tool result references call_id "${callId}" without prior tool_call.`,
          eventIndex: index,
          eventId,
          eventType: type,
        })
      } else {
        unresolvedToolCalls.delete(callId)
      }
    }

    if (type === "completion") completionSeen = true
    if (type === "run_finished") runFinishedSeen = true
  })

  if (unresolvedToolCalls.size > 0) {
    for (const callId of unresolvedToolCalls) {
      errors.push({
        code: "tool-call-without-result",
        message: `Tool call "${callId}" did not receive a tool_result.`,
      })
    }
  }

  if (!completionSeen && !runFinishedSeen && events.length > 0) {
    warnings.push({
      code: "no-completion",
      message: "No completion or run_finished event observed in stream.",
    })
  }

  if (seqMonotonic === null) seqMonotonic = null
  if (timestampMonotonic === null) timestampMonotonic = null

  return {
    summary: {
      eventCount: events.length,
      toolCallCount,
      toolResultCount,
      untrackedToolCalls,
      untrackedToolResults,
      completionSeen,
      runFinishedSeen,
      seqMonotonic,
      timestampMonotonic,
      warningsCount: warnings.length,
      errorsCount: errors.length,
    },
    errors,
    warnings,
  }
}
