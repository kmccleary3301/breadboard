export type NormalizedEvent = {
  seq: number
  eventId?: string
  type: string
  timestamp?: number
  actor?: { kind: "user" | "assistant" | "tool" | "system"; agentId?: string }
  messageId?: string
  toolCallId?: string
  textDelta?: string
  toolName?: string
  argsText?: string
  argsObject?: unknown
  toolResult?: unknown
  ok?: boolean
  error?: { message: string; code?: string; data?: unknown }
  usage?: { inputTokens?: number; outputTokens?: number; totalTokens?: number }
  spanId?: string
  parentSpanId?: string
}
