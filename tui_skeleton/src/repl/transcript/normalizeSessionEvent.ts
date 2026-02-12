import type { SessionEvent } from "../../api/types.js"
import type { NormalizedEvent } from "./normalizedEvent.js"

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null

const extractString = (value: unknown, keys: string[]): string | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    if (typeof raw === "string" && raw.trim().length > 0) return raw
  }
  return undefined
}

const extractBool = (value: unknown, keys: string[]): boolean | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    if (typeof raw === "boolean") return raw
  }
  return undefined
}

const extractNumber = (value: unknown, keys: string[]): number | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    if (typeof raw === "number" && Number.isFinite(raw)) return raw
  }
  return undefined
}

export const normalizeSessionEvent = (event: SessionEvent): NormalizedEvent | null => {
  const payload = isRecord(event.payload) ? event.payload : {}
  const seq =
    typeof event.seq === "number" && Number.isFinite(event.seq)
      ? event.seq
      : /^\d+$/.test(event.id)
        ? Number(event.id)
        : Number.isFinite(event.timestamp)
          ? event.timestamp
          : 0

  const base: NormalizedEvent = {
    seq,
    eventId: event.id,
    type: event.type,
    timestamp: event.timestamp ?? event.timestamp_ms,
    spanId: typeof event.span_id === "string" ? event.span_id : undefined,
    parentSpanId: typeof event.parent_span_id === "string" ? event.parent_span_id : undefined,
  }

  if (event.type === "assistant.message.start" || event.type === "assistant.message.delta" || event.type === "assistant.message.end") {
    return {
      ...base,
      actor: { kind: "assistant" },
      messageId: extractString(payload, ["message_id", "id"]),
      textDelta: extractString(payload, ["delta", "text"]),
    }
  }
  if (event.type === "assistant_delta") {
    return {
      ...base,
      actor: { kind: "assistant" },
      messageId: extractString(payload, ["message_id", "id"]),
      textDelta: extractString(payload, ["delta", "text", "content"]),
    }
  }
  if (event.type === "assistant_message") {
    return {
      ...base,
      actor: { kind: "assistant" },
      messageId: extractString(payload, ["message_id", "id"]),
      textDelta: extractString(payload, ["text", "content", "message"]),
    }
  }
  if (event.type === "user.message" || event.type === "user_message") {
    return {
      ...base,
      actor: { kind: "user" },
      messageId: extractString(payload, ["message_id", "id"]),
      textDelta: extractString(payload, ["text", "content", "message"]),
    }
  }
  if (event.type === "assistant.tool_call.start" || event.type === "assistant.tool_call.delta" || event.type === "assistant.tool_call.end") {
    return {
      ...base,
      actor: { kind: "tool" },
      toolCallId: extractString(payload, ["tool_call_id", "toolCallId", "call_id", "callId", "id"]),
      toolName: extractString(payload, ["tool_name", "tool", "name", "command"]),
      argsText: extractString(payload, ["args_text", "args"]),
      textDelta: extractString(payload, ["args_text_delta", "delta", "text"]),
    }
  }
  if (event.type === "tool.result" || event.type === "tool_result") {
    return {
      ...base,
      actor: { kind: "tool" },
      toolCallId: extractString(payload, ["tool_call_id", "toolCallId", "call_id", "callId", "id"]),
      toolName: extractString(payload, ["tool_name", "tool", "name", "command"]),
      toolResult: payload,
      ok: extractBool(payload, ["ok", "success"]),
    }
  }
  if (event.type === "warning" || event.type === "error") {
    const message = extractString(payload, ["message", "detail", "summary"]) ?? JSON.stringify(payload)
    return {
      ...base,
      actor: { kind: "system" },
      error: { message },
    }
  }
  if (event.type === "log_link") {
    return {
      ...base,
      actor: { kind: "system" },
      textDelta: extractString(payload, ["url", "href", "path"]),
    }
  }
  if (event.type === "reward_update") {
    return {
      ...base,
      actor: { kind: "system" },
      textDelta: JSON.stringify(payload),
    }
  }
  if (event.type === "usage.update") {
    return {
      ...base,
      actor: { kind: "system" },
      usage: {
        inputTokens: extractNumber(payload, ["prompt_tokens", "input_tokens"]),
        outputTokens: extractNumber(payload, ["completion_tokens", "output_tokens"]),
        totalTokens: extractNumber(payload, ["total_tokens"]),
      },
    }
  }
  if (
    event.type.startsWith("run.") ||
    event.type.startsWith("session.") ||
    event.type.startsWith("agent.") ||
    event.type.startsWith("turn") ||
    event.type.startsWith("checkpoint") ||
    event.type === "task_event" ||
    event.type === "ctree_snapshot" ||
    event.type === "ctree_node"
  ) {
    const summary =
      extractString(payload, ["summary", "message", "detail", "status", "reason"]) ?? JSON.stringify(payload)
    return {
      ...base,
      actor: { kind: "system" },
      textDelta: summary,
    }
  }
  return base
}
