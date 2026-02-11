import type { ConversationEntry, ToolLogEntry } from "./types.js"
import {
  type TranscriptItem,
  type TranscriptMessageItem,
  type TranscriptSystemItem,
  type TranscriptToolItem,
  makeTranscriptId,
} from "./transcriptModel.js"
import type { NormalizedEvent } from "./transcript/normalizedEvent.js"

type AssistantSegment = { messageId: string; segment: number; text: string; createdAt: number }

export interface TranscriptBuildOptions {
  readonly includeRawEvents?: boolean
  readonly pendingToolsInTail?: boolean
}

export interface TranscriptBuildResult {
  readonly committed: TranscriptItem[]
  readonly tail: TranscriptItem[]
}

const partitionConversation = (
  entries: ReadonlyArray<ConversationEntry>,
): { finals: ConversationEntry[]; streaming: ConversationEntry | null } => {
  const finals: ConversationEntry[] = []
  let streaming: ConversationEntry | null = null
  for (const entry of entries) {
    if (entry.phase === "streaming") {
      streaming = entry
    } else {
      finals.push(entry)
    }
  }
  return { finals, streaming }
}

const classifyToolEntry = (
  entry: ToolLogEntry,
  source: "tool" | "raw",
): TranscriptToolItem | TranscriptSystemItem => {
  if (entry.kind === "status" || entry.kind === "error" || entry.kind === "reward" || entry.kind === "completion") {
    return {
      id: makeTranscriptId("sys", entry.id),
      kind: "system",
      systemKind: entry.kind,
      text: entry.text,
      status: entry.status,
      createdAt: entry.createdAt,
      source: source === "raw" ? "raw" : "system",
    }
  }
  return {
    id: makeTranscriptId("tool", entry.id),
    kind: "tool",
    toolKind: entry.kind,
    text: entry.text,
    status: entry.status,
    callId: entry.callId ?? null,
    display: entry.display ?? null,
    createdAt: entry.createdAt,
    source: source === "raw" ? "raw" : "tool",
  }
}

const toMessageItem = (entry: ConversationEntry, streaming = false): TranscriptMessageItem => ({
  id: makeTranscriptId("msg", entry.id),
  kind: "message",
  speaker: entry.speaker,
  text: entry.text,
  phase: entry.phase,
  richBlocks: entry.richBlocks,
  markdownStreaming: entry.markdownStreaming,
  markdownError: entry.markdownError,
  createdAt: entry.createdAt,
  source: "conversation",
  streaming,
})

const stableMerge = (items: TranscriptItem[]): TranscriptItem[] => {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      if (a.item.createdAt !== b.item.createdAt) return a.item.createdAt - b.item.createdAt
      return a.index - b.index
    })
    .map(({ item }) => item)
}

export interface TranscriptInput {
  readonly conversation: ReadonlyArray<ConversationEntry>
  readonly toolEvents: ReadonlyArray<ToolLogEntry>
  readonly rawEvents?: ReadonlyArray<ToolLogEntry>
}

export const buildTranscript = (input: TranscriptInput, options: TranscriptBuildOptions = {}): TranscriptBuildResult => {
  const includeRaw = options.includeRawEvents === true
  const pendingInTail = options.pendingToolsInTail !== false
  const { finals, streaming } = partitionConversation(input.conversation)

  const committedItems: TranscriptItem[] = []
  const tailItems: TranscriptItem[] = []

  finals.forEach((entry) => committedItems.push(toMessageItem(entry)))
  if (streaming) {
    tailItems.push(toMessageItem(streaming, true))
  }

  const handleToolEntries = (entries: ReadonlyArray<ToolLogEntry>, source: "tool" | "raw") => {
    for (const entry of entries) {
      const item = classifyToolEntry(entry, source)
      const pending = entry.status === "pending"
      if (pending && pendingInTail) {
        tailItems.push({ ...item, streaming: true })
      } else {
        committedItems.push(item)
      }
    }
  }

  handleToolEntries(input.toolEvents ?? [], "tool")
  if (includeRaw && input.rawEvents?.length) {
    handleToolEntries(input.rawEvents, "raw")
  }

  return {
    committed: stableMerge(committedItems),
    tail: stableMerge(tailItems),
  }
}

const formatToolHeader = (toolName?: string | null, argsText?: string | null): string => {
  const name = toolName?.trim() || "Tool"
  if (!argsText) return name
  const trimmed = argsText.replace(/\s+/g, " ").trim()
  const preview = trimmed.length > 80 ? `${trimmed.slice(0, 77)}…` : trimmed
  return `${name}(${preview})`
}

export const buildTranscriptFromEvents = (events: ReadonlyArray<NormalizedEvent>): TranscriptBuildResult => {
  const committed: TranscriptItem[] = []
  const tail: TranscriptItem[] = []
  const toolBuffers = new Map<
    string,
    { text: string; createdAt: number; status?: "pending" | "success" | "error"; toolName?: string | null }
  >()
  const assistantSegments = new Map<string, number>()
  const closedAssistantMessages = new Set<string>()
  let activeAssistant: AssistantSegment | null = null
  const seenEventIds = new Set<string>()
  const finalizedTools = new Set<string>()

  const ordered = [...events].sort((a, b) => a.seq - b.seq)

  const systemTypes = new Set([
    "run.start",
    "run.end",
    "run_finished",
    "session.start",
    "session.meta",
    "agent.spawn",
    "agent.status",
    "agent.end",
    "turn_start",
    "checkpoint_list",
    "checkpoint_restored",
    "task_event",
    "ctree_snapshot",
    "ctree_node",
  ])
  const suppressedSystemTypes = new Set([
    "turn_start",
    "turn_started",
    "turn.start",
    "turn.end",
    "turn_finish",
    "turn_finished",
    "turn.finish",
  ])

  const finalizeAssistantSegment = (): void => {
    if (!activeAssistant) return
    committed.push({
      id: makeTranscriptId("msg", `${activeAssistant.messageId}#${activeAssistant.segment}`),
      kind: "message",
      speaker: "assistant",
      text: activeAssistant.text,
      phase: "final",
      createdAt: activeAssistant.createdAt,
      source: "conversation",
    })
    activeAssistant = null
  }

  const startAssistantSegment = (event: NormalizedEvent): void => {
    const messageId = event.messageId ?? `assistant-${event.seq}`
    const nextSegment = assistantSegments.get(messageId) ?? 0
    assistantSegments.set(messageId, nextSegment + 1)
    activeAssistant = {
      messageId,
      segment: nextSegment,
      text: "",
      createdAt: event.timestamp ?? event.seq,
    }
  }

  for (const event of ordered) {
    if (event.eventId) {
      if (seenEventIds.has(event.eventId)) continue
      seenEventIds.add(event.eventId)
    }
    const isAssistant = event.actor?.kind === "assistant" && Boolean(event.messageId)
    const isAssistantDelta =
      isAssistant &&
      (event.type === "assistant.message.delta" ||
        event.type === "assistant_delta" ||
        event.type === "assistant.message.start" ||
        event.type === "assistant.message.end" ||
        typeof event.textDelta === "string")

    if (!isAssistantDelta && activeAssistant) {
      // Non-assistant top-level event segments the assistant stream.
      finalizeAssistantSegment()
    }

    if (isAssistant) {
      const messageId = event.messageId as string
      if (closedAssistantMessages.has(messageId)) {
        continue
      }
      const current = activeAssistant as AssistantSegment | null
      if (!current || current.messageId !== messageId) {
        if (current) finalizeAssistantSegment()
        startAssistantSegment(event)
      }
      if (event.type === "assistant.message.start" && !activeAssistant) {
        startAssistantSegment(event)
      }
      const delta = event.textDelta ?? ""
      if (delta) {
        activeAssistant!.text += delta
      }
      if (event.type === "assistant_message") {
        const text = event.textDelta ?? ""
        if (!activeAssistant) startAssistantSegment(event)
        activeAssistant!.text += text
        finalizeAssistantSegment()
        closedAssistantMessages.add(messageId)
      } else if (event.type === "assistant.message.end" || event.type.endsWith(".end")) {
        finalizeAssistantSegment()
        closedAssistantMessages.add(messageId)
      }
      continue
    }
    if (event.actor?.kind === "user") {
      if (activeAssistant) finalizeAssistantSegment()
      const text = event.textDelta ?? ""
      const id = event.messageId ?? `user-${event.seq}`
      const item: TranscriptMessageItem = {
        id: makeTranscriptId("msg", id),
        kind: "message",
        speaker: "user",
        text,
        phase: "final",
        createdAt: event.timestamp ?? event.seq,
        source: "conversation",
      }
      committed.push(item)
      continue
    }
    if (event.toolCallId) {
      if (activeAssistant) finalizeAssistantSegment()
      if (finalizedTools.has(event.toolCallId)) continue
      const buffer = toolBuffers.get(event.toolCallId) ?? {
        text: formatToolHeader(event.toolName, event.argsText ?? null),
        createdAt: event.timestamp ?? event.seq,
        status: "pending" as const,
        toolName: event.toolName ?? null,
      }
      if (event.textDelta) {
        buffer.text = formatToolHeader(buffer.toolName, (event.argsText ?? "") + event.textDelta)
      } else if (event.argsText) {
        buffer.text = formatToolHeader(buffer.toolName, event.argsText)
      }
      toolBuffers.set(event.toolCallId, buffer)
      if (event.type === "tool.result" || event.type === "tool_result") {
        const status = event.ok === false || event.error ? "error" : "success"
        const item: TranscriptToolItem = {
          id: makeTranscriptId("tool", event.toolCallId),
          kind: "tool",
          toolKind: "result",
          text: buffer.text,
          status,
          callId: event.toolCallId,
          createdAt: buffer.createdAt,
          source: "tool",
        }
        committed.push(item)
        toolBuffers.delete(event.toolCallId)
        finalizedTools.add(event.toolCallId)
      }
      continue
    }
    let handledSystem = false
    if (event.error) {
      if (activeAssistant) finalizeAssistantSegment()
      const item: TranscriptSystemItem = {
        id: makeTranscriptId("sys", event.eventId ?? `err-${event.seq}`),
        kind: "system",
        systemKind: "error",
        text: event.error.message,
        status: "error",
        createdAt: event.timestamp ?? event.seq,
        source: "system",
      }
      committed.push(item)
      handledSystem = true
    }
    if (event.usage) {
      if (activeAssistant) finalizeAssistantSegment()
      const summary = [
        event.usage.totalTokens ? `tok ${event.usage.totalTokens}` : null,
        event.usage.inputTokens ? `in ${event.usage.inputTokens}` : null,
        event.usage.outputTokens ? `out ${event.usage.outputTokens}` : null,
      ]
        .filter(Boolean)
        .join(" · ")
      committed.push({
        id: makeTranscriptId("sys", event.eventId ?? `usage-${event.seq}`),
        kind: "system",
        systemKind: "usage",
        text: summary || "Usage update",
        status: "success",
        createdAt: event.timestamp ?? event.seq,
        source: "system",
      })
      handledSystem = true
    }
    if (event.type === "log_link" && event.textDelta) {
      if (activeAssistant) finalizeAssistantSegment()
      committed.push({
        id: makeTranscriptId("sys", event.eventId ?? `log-${event.seq}`),
        kind: "system",
        systemKind: "log",
        text: event.textDelta,
        status: "success",
        createdAt: event.timestamp ?? event.seq,
        source: "system",
      })
      handledSystem = true
    }
    if (event.type === "reward_update" && event.textDelta) {
      if (activeAssistant) finalizeAssistantSegment()
      committed.push({
        id: makeTranscriptId("sys", event.eventId ?? `reward-${event.seq}`),
        kind: "system",
        systemKind: "reward",
        text: event.textDelta,
        status: "success",
        createdAt: event.timestamp ?? event.seq,
        source: "system",
      })
      handledSystem = true
    }
    if (event.type.startsWith("permission")) {
      if (activeAssistant) finalizeAssistantSegment()
      const label = event.type.replace(/[_\.]+/g, " ").trim()
      committed.push({
        id: makeTranscriptId("sys", event.eventId ?? `perm-${event.seq}`),
        kind: "system",
        systemKind: "notice",
        text: label,
        status: "pending",
        createdAt: event.timestamp ?? event.seq,
        source: "system",
      })
      handledSystem = true
    }
    const suppressSystemLine = suppressedSystemTypes.has(event.type) || event.type.startsWith("turn")
    if (
      !handledSystem &&
      !suppressSystemLine &&
      event.textDelta &&
      (event.actor?.kind === "system" || systemTypes.has(event.type))
    ) {
      if (activeAssistant) finalizeAssistantSegment()
      committed.push({
        id: makeTranscriptId("sys", event.eventId ?? `sys-${event.seq}`),
        kind: "system",
        systemKind: "status",
        text: event.textDelta,
        status: "success",
        createdAt: event.timestamp ?? event.seq,
        source: "system",
      })
    }
  }

  if (activeAssistant) {
    const current = activeAssistant as AssistantSegment
    tail.push({
      id: makeTranscriptId("msg", `${current.messageId}#${current.segment}`),
      kind: "message",
      speaker: "assistant",
      text: current.text,
      phase: "streaming",
      createdAt: current.createdAt,
      source: "conversation",
      streaming: true,
    })
  }
  for (const [toolCallId, buffer] of toolBuffers.entries()) {
    tail.push({
      id: makeTranscriptId("tool", toolCallId),
      kind: "tool",
      toolKind: "call",
      text: buffer.text,
      status: buffer.status ?? "pending",
      callId: toolCallId,
      createdAt: buffer.createdAt,
      source: "tool",
      streaming: true,
    })
  }

  return {
    committed: stableMerge(committed),
    tail: stableMerge(tail),
  }
}
