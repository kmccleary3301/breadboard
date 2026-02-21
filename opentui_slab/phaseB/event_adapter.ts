import type { BridgeEvent } from "./bridge.ts"

export type NormalizedEventType =
  | "message.delta"
  | "message.final"
  | "tool.call"
  | "tool.result"
  | "permission.request"
  | "permission.response"
  | "task.update"
  | "warning"
  | "run.finished"
  | "error"
  | "unknown"

export type NormalizedActorKind = "user" | "assistant" | "tool" | "system"

export type NormalizedEventActor = {
  readonly kind: NormalizedActorKind
}

export type NormalizedEventSummary = {
  readonly title: string
  readonly short: string
  readonly detail?: string
}

export type SlabDisplayLane = "user" | "assistant" | "tool" | "permission" | "system"
export type SlabDisplayTone = "neutral" | "info" | "success" | "warning" | "danger"
export type SlabDisplayPriority = "low" | "normal" | "high"

export type SlabDisplayHints = {
  readonly lane: SlabDisplayLane
  readonly badge: string
  readonly tone: SlabDisplayTone
  readonly priority: SlabDisplayPriority
  readonly stream: boolean
}

export type ToolRenderPolicy = {
  readonly mode: "compact" | "expanded"
  readonly reason: string
}

export type ToolClass = "context_collection" | "default"

export type ArtifactRefSummary = {
  readonly id: string
  readonly kind: string
  readonly mime?: string
  readonly path?: string
  readonly sizeBytes?: number
  readonly sha256?: string
  readonly note?: string
  readonly previewLines?: ReadonlyArray<string>
}

export type OverlayIntent = {
  readonly kind: "permission" | "task"
  readonly action: "open" | "update" | "close"
  readonly requestId?: string
  readonly taskId?: string
}

export type NormalizedEvent = {
  readonly seq: number
  readonly eventId?: string
  readonly rawType: string
  readonly type: NormalizedEventType
  readonly timestamp?: number
  readonly sessionId?: string
  readonly turn?: number
  readonly actor?: NormalizedEventActor
  readonly messageId?: string
  readonly toolCallId?: string
  readonly toolName?: string
  readonly toolClass?: ToolClass
  readonly requestId?: string
  readonly decision?: string
  readonly taskId?: string
  readonly taskStatus?: string
  readonly taskDescription?: string
  readonly taskLaneId?: string
  readonly taskLaneLabel?: string
  readonly childSessionId?: string
  readonly parentSessionId?: string
  readonly taskSubagentId?: string
  readonly taskSubagentLabel?: string
  readonly textDelta?: string
  readonly argsText?: string
  readonly toolResult?: unknown
  readonly artifactRef?: ArtifactRefSummary
  readonly ok?: boolean
  readonly error?: {
    readonly message: string
    readonly code?: string
  }
  readonly payload: Record<string, unknown>
  readonly summary: NormalizedEventSummary
  readonly hints: SlabDisplayHints
  readonly toolRenderPolicy?: ToolRenderPolicy
  readonly overlayIntent?: OverlayIntent
}

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const toPayloadRecord = (payload: BridgeEvent["payload"]): Record<string, unknown> => (isRecord(payload) ? payload : {})

const toFiniteNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined

export const normalizeEventSeq = (event: BridgeEvent): number => {
  const seq = toFiniteNumber(event.seq)
  if (seq !== undefined) return seq

  if (typeof event.id === "string" && /^\d+$/.test(event.id)) {
    return Number(event.id)
  }

  const tsMs = toFiniteNumber(event.timestamp_ms)
  if (tsMs !== undefined) return tsMs

  const ts = toFiniteNumber(event.timestamp)
  if (ts !== undefined) return ts

  return 0
}

const normalizeTimestamp = (event: BridgeEvent): number | undefined => {
  const tsMs = toFiniteNumber(event.timestamp_ms)
  if (tsMs !== undefined) return tsMs
  return toFiniteNumber(event.timestamp)
}

export const extractString = (value: unknown, keys: readonly string[]): string | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    if (typeof raw === "string") {
      const trimmed = raw.trim()
      if (trimmed.length > 0) return trimmed
    }
  }
  return undefined
}

export const extractNumber = (value: unknown, keys: readonly string[]): number | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    const num = toFiniteNumber(raw)
    if (num !== undefined) return num
  }
  return undefined
}

export const extractBoolean = (value: unknown, keys: readonly string[]): boolean | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    if (typeof raw === "boolean") return raw
  }
  return undefined
}

export const extractRecord = (value: unknown, keys: readonly string[]): Record<string, unknown> | undefined => {
  if (!isRecord(value)) return undefined
  for (const key of keys) {
    const raw = value[key]
    if (isRecord(raw)) return raw
  }
  return undefined
}

const stableClone = (value: unknown, seen: WeakSet<object>): unknown => {
  if (Array.isArray(value)) {
    return value.map((entry) => stableClone(entry, seen))
  }
  if (isRecord(value)) {
    if (seen.has(value)) return "[Circular]"
    seen.add(value)
    const out: Record<string, unknown> = {}
    for (const key of Object.keys(value).sort()) {
      out[key] = stableClone(value[key], seen)
    }
    seen.delete(value)
    return out
  }
  return value
}

export const stableStringify = (value: unknown): string => {
  try {
    const normalized = stableClone(value, new WeakSet<object>())
    return JSON.stringify(normalized)
  } catch {
    return String(value)
  }
}

const compactWhitespace = (text: string): string => text.replace(/\s+/g, " ").trim()

export const summarizeText = (text: string, maxLen = 96): string => {
  const compact = compactWhitespace(text)
  if (compact.length <= maxLen) return compact
  const clipped = compact.slice(0, Math.max(0, maxLen - 3)).trimEnd()
  return `${clipped}...`
}

const hasPayloadContent = (payload: Record<string, unknown>): boolean => Object.keys(payload).length > 0

const CONTEXT_COLLECTION_TOOL_NAMES = new Set(
  [
    "read_file",
    "read",
    "cat",
    "ls",
    "list_dir",
    "glob",
    "grep",
    "search",
    "find",
    "rg",
    "view",
  ].map((name) => name.toLowerCase()),
)

export const classifyToolName = (toolName?: string): ToolClass => {
  const normalized = toolName?.trim().toLowerCase()
  if (!normalized) return "default"
  if (CONTEXT_COLLECTION_TOOL_NAMES.has(normalized)) return "context_collection"
  if (
    normalized.startsWith("read_") ||
    normalized.startsWith("list_") ||
    normalized.startsWith("search_") ||
    normalized.startsWith("grep_") ||
    normalized.startsWith("glob_")
  ) {
    return "context_collection"
  }
  return "default"
}

const buildBase = (event: BridgeEvent): Omit<NormalizedEvent, "type" | "actor" | "summary" | "hints"> => {
  const turn = toFiniteNumber(event.turn)
  return {
    seq: normalizeEventSeq(event),
    eventId: typeof event.id === "string" ? event.id : undefined,
    rawType: event.type,
    timestamp: normalizeTimestamp(event),
    sessionId: typeof event.session_id === "string" ? event.session_id : undefined,
    turn,
    payload: toPayloadRecord(event.payload),
  }
}

const normalizeDecision = (decision: string | undefined): string | undefined => {
  if (!decision) return undefined
  const trimmed = decision.trim().toLowerCase()
  return trimmed || undefined
}

export const deriveSlabDisplayHints = (
  type: NormalizedEventType,
  options: {
    readonly rawType?: string
    readonly stream?: boolean
    readonly ok?: boolean
    readonly decision?: string
  } = {},
): SlabDisplayHints => {
  switch (type) {
    case "message.delta":
      return { lane: "assistant", badge: "assistant", tone: "info", priority: "low", stream: true }
    case "message.final":
      return { lane: "assistant", badge: "assistant", tone: "info", priority: "normal", stream: false }
    case "tool.call":
      return { lane: "tool", badge: "tool", tone: "info", priority: "normal", stream: false }
    case "tool.result": {
      const tone: SlabDisplayTone = options.ok === false ? "danger" : options.ok === true ? "success" : "neutral"
      return { lane: "tool", badge: "tool", tone, priority: "normal", stream: false }
    }
    case "permission.request":
      return { lane: "permission", badge: "permission", tone: "warning", priority: "high", stream: false }
    case "permission.response": {
      const decision = normalizeDecision(options.decision)
      const tone: SlabDisplayTone =
        decision === "allow" || decision === "approved" || decision === "approve"
          ? "success"
          : decision === "deny" || decision === "denied" || decision === "reject" || decision === "rejected"
            ? "danger"
            : "neutral"
      return { lane: "permission", badge: "permission", tone, priority: "high", stream: false }
    }
    case "task.update": {
      const decision = normalizeDecision(options.decision)
      const tone: SlabDisplayTone =
        decision === "completed" || decision === "done" || decision === "success"
          ? "success"
          : decision === "failed" || decision === "error" || decision === "cancelled"
            ? "danger"
            : "info"
      const priority: SlabDisplayPriority = tone === "danger" ? "high" : "normal"
      return { lane: "system", badge: "task", tone, priority, stream: false }
    }
    case "warning":
      return { lane: "system", badge: "warning", tone: "warning", priority: "normal", stream: false }
    case "run.finished": {
      const tone: SlabDisplayTone = options.ok === true ? "success" : options.ok === false ? "warning" : "neutral"
      return { lane: "system", badge: "run", tone, priority: "high", stream: false }
    }
    case "error":
      return { lane: "system", badge: "error", tone: "danger", priority: "high", stream: false }
    case "unknown":
    default:
      return {
        lane: "system",
        badge: options.rawType?.trim() || "event",
        tone: "neutral",
        priority: "low",
        stream: Boolean(options.stream),
      }
  }
}

const assistantText = (payload: Record<string, unknown>, preferDelta = false): string | undefined =>
  preferDelta
    ? extractString(payload, ["delta", "text", "content", "message"])
    : extractString(payload, ["text", "content", "message", "delta"])

const resolveToolCall = (
  payload: Record<string, unknown>,
): { toolCallId?: string; toolName?: string; argsText?: string } => {
  const call = extractRecord(payload, ["call"])
  const source = call ?? payload
  const toolCallId = extractString(source, ["tool_call_id", "toolCallId", "call_id", "callId", "id"])
  const toolName = extractString(source, ["tool_name", "tool", "name", "command"])

  const argsRaw = source.arguments ?? source.args ?? source.args_text ?? source.input
  const argsText =
    typeof argsRaw === "string"
      ? argsRaw.trim() || undefined
      : argsRaw === undefined
        ? undefined
        : stableStringify(argsRaw)

  return { toolCallId, toolName, argsText }
}

const parseArtifactRef = (value: unknown): ArtifactRefSummary | undefined => {
  if (!isRecord(value)) return undefined
  const schemaVersion = extractString(value, ["schema_version", "schemaVersion"])
  if (schemaVersion !== "artifact_ref_v1") return undefined
  const id = extractString(value, ["id"])
  const kind = extractString(value, ["kind"])
  if (!id || !kind) return undefined
  const preview = extractRecord(value, ["preview"])
  const lines = Array.isArray(preview?.lines)
    ? preview!.lines.filter((line): line is string => typeof line === "string")
    : []
  return {
    id,
    kind,
    mime: extractString(value, ["mime"]),
    path: extractString(value, ["path"]),
    sizeBytes: extractNumber(value, ["size_bytes", "sizeBytes"]),
    sha256: extractString(value, ["sha256"]),
    note: extractString(preview, ["note"]),
    previewLines: lines.length > 0 ? lines.slice(0, 3) : undefined,
  }
}

const extractArtifactRef = (payload: Record<string, unknown>): ArtifactRefSummary | undefined => {
  const directCandidates: unknown[] = [
    payload.detail_artifact,
    payload.detailArtifact,
    payload.artifact_ref,
    payload.artifactRef,
    payload.artifact,
  ]
  for (const candidate of directCandidates) {
    const parsed = parseArtifactRef(candidate)
    if (parsed) return parsed
  }

  const display = extractRecord(payload, ["display"])
  if (display) {
    const parsedFromDisplay = extractArtifactRef(display)
    if (parsedFromDisplay) return parsedFromDisplay
  }

  const result = extractRecord(payload, ["result"])
  if (result) {
    const parsedFromResult = extractArtifactRef(result)
    if (parsedFromResult) return parsedFromResult
  }

  return undefined
}

const resolveToolResult = (
  payload: Record<string, unknown>,
): {
  toolCallId?: string
  toolName?: string
  toolResult: unknown
  artifactRef?: ArtifactRefSummary
  ok?: boolean
} => {
  const result = extractRecord(payload, ["result"])
  const source = result ?? payload
  const toolCallId = extractString(source, ["tool_call_id", "toolCallId", "call_id", "callId", "id"])
  const toolName = extractString(source, ["tool_name", "tool", "name", "command"])
  const ok = extractBoolean(source, ["ok", "success", "completed"])
  return {
    toolCallId,
    toolName,
    ok,
    toolResult: result ?? payload,
    artifactRef: extractArtifactRef(payload),
  }
}

export const deriveToolRenderPolicy = (
  normalized: Pick<NormalizedEvent, "type" | "argsText" | "toolResult" | "toolClass" | "artifactRef">,
): ToolRenderPolicy | undefined => {
  if (normalized.artifactRef) {
    const kind = normalized.artifactRef.kind.toLowerCase()
    const mime = (normalized.artifactRef.mime ?? "").toLowerCase()
    if (kind.includes("diff") || mime.includes("diff")) {
      return { mode: "expanded", reason: "artifact-diff" }
    }
    return { mode: "compact", reason: "artifact-ref" }
  }

  if (normalized.toolClass === "context_collection") {
    if (normalized.type === "tool.result") {
      const rendered = normalized.toolResult == null ? "" : stableStringify(normalized.toolResult)
      if (rendered.length > 1200) return { mode: "expanded", reason: "context-collection-result-huge" }
      return { mode: "compact", reason: "context-collection" }
    }
    if (normalized.type === "tool.call") {
      return { mode: "compact", reason: "context-collection" }
    }
  }

  if (normalized.type === "tool.call") {
    const length = normalized.argsText?.length ?? 0
    if (length > 220) return { mode: "expanded", reason: "tool-call-args-large" }
    return { mode: "compact", reason: "tool-call-default" }
  }
  if (normalized.type === "tool.result") {
    const raw = normalized.toolResult
    const rendered = raw == null ? "" : stableStringify(raw)
    if (rendered.length > 260 || rendered.includes("\\n")) {
      return { mode: "expanded", reason: "tool-result-large" }
    }
    return { mode: "compact", reason: "tool-result-default" }
  }
  return undefined
}

export const formatNormalizedEventForStdout = (normalized: NormalizedEvent): string | null => {
  switch (normalized.type) {
    case "tool.call": {
      const name = normalized.toolName ?? "tool_call"
      const label = normalized.toolClass === "context_collection" ? "context" : "tool"
      if (normalized.toolRenderPolicy?.mode === "expanded") {
        const args = normalized.argsText ? summarizeText(normalized.argsText, 320) : "(no args)"
        return `\n[${label}] ${name} (expanded)\nargs: ${args}\n`
      }
      return `\n[${label}] ${name}\n`
    }
    case "tool.result": {
      const name = normalized.toolName ?? "tool_result"
      const label = normalized.toolClass === "context_collection" ? "context" : "tool"
      const status = normalized.ok === false ? "failed" : normalized.ok === true ? "ok" : "result"
      if (normalized.artifactRef) {
        const artifact = normalized.artifactRef
        const sizeBit = typeof artifact.sizeBytes === "number" ? ` (${artifact.sizeBytes} bytes)` : ""
        const location = artifact.path ? ` · ${artifact.path}` : ""
        const note = artifact.note ? `\nnote: ${artifact.note}` : ""
        if (normalized.toolRenderPolicy?.mode === "expanded") {
          const preview = artifact.previewLines?.length
            ? `\npreview:\n${artifact.previewLines.map((line) => `  ${line}`).join("\n")}`
            : ""
          return `\n[${label}] ${name} (${status}, artifact)\nartifact: ${artifact.kind}${location}${sizeBit}${preview}${note}\n`
        }
        return `\n[${label}] ${name} (${status}, artifact) · ${artifact.kind}${location}${sizeBit}${note}\n`
      }
      if (normalized.toolRenderPolicy?.mode === "expanded") {
        const detail = summarizeText(stableStringify(normalized.toolResult ?? {}), 320)
        return `\n[${label}] ${name} (${status}, expanded)\n${detail}\n`
      }
      return `\n[${label}] ${name} (${status})\n`
    }
    case "task.update":
      return `\n[task] ${normalized.summary.short}\n`
    case "warning":
      return `\n[warning] ${normalized.summary.short}\n`
    default:
      return null
  }
}

export const normalizeBridgeEvent = (event: BridgeEvent): NormalizedEvent => {
  const base = buildBase(event)
  const payload = base.payload

  switch (event.type) {
    case "assistant_delta":
    case "assistant.message.delta":
    case "assistant.thought_summary.delta": {
      const messageId = extractString(payload, ["message_id", "messageId", "id"])
      const textDelta = assistantText(payload, true)
      return {
        ...base,
        type: "message.delta",
        actor: { kind: "assistant" },
        messageId,
        textDelta,
        summary: {
          title: "Assistant delta",
          short: textDelta ? summarizeText(textDelta) : "(empty assistant delta)",
          detail: messageId ? `message_id=${messageId}` : undefined,
        },
        hints: deriveSlabDisplayHints("message.delta", { stream: true }),
      }
    }

    case "assistant_message":
    case "assistant.message.end":
    case "assistant.message.start": {
      const messageId = extractString(payload, ["message_id", "messageId", "id"])
      const textDelta = assistantText(payload)
      const isStart = event.type === "assistant.message.start"
      return {
        ...base,
        type: isStart ? "message.delta" : "message.final",
        actor: { kind: "assistant" },
        messageId,
        textDelta,
        summary: {
          title: isStart ? "Assistant message start" : "Assistant message",
          short: textDelta ? summarizeText(textDelta) : isStart ? "assistant response started" : "(empty assistant message)",
          detail: messageId ? `message_id=${messageId}` : undefined,
        },
        hints: deriveSlabDisplayHints(isStart ? "message.delta" : "message.final"),
      }
    }

    case "user_message": {
      const messageId = extractString(payload, ["message_id", "messageId", "id"])
      const textDelta = extractString(payload, ["text", "content", "message"])
      return {
        ...base,
        type: "message.final",
        actor: { kind: "user" },
        messageId,
        textDelta,
        summary: {
          title: "User message",
          short: textDelta ? summarizeText(textDelta) : "(empty user message)",
          detail: messageId ? `message_id=${messageId}` : undefined,
        },
        hints: { lane: "user", badge: "user", tone: "neutral", priority: "normal", stream: false },
      }
    }

    case "tool_call": {
      const { toolCallId, toolName, argsText } = resolveToolCall(payload)
      const toolClass = classifyToolName(toolName)
      const short = toolName ? `call ${toolName}` : toolCallId ? `tool call ${toolCallId}` : "tool call"
      const normalized: NormalizedEvent = {
        ...base,
        type: "tool.call",
        actor: { kind: "tool" },
        toolCallId,
        toolName,
        toolClass,
        argsText,
        summary: {
          title: "Tool call",
          short,
          detail: argsText ? summarizeText(argsText, 140) : undefined,
        },
        hints: deriveSlabDisplayHints("tool.call"),
      }
      return { ...normalized, toolRenderPolicy: deriveToolRenderPolicy(normalized) }
    }

    case "tool_result": {
      const { toolCallId, toolName, toolResult, artifactRef, ok } = resolveToolResult(payload)
      const toolClass = classifyToolName(toolName)
      const short = toolName
        ? ok === true
          ? `${toolName} succeeded`
          : ok === false
            ? `${toolName} failed`
            : `${toolName} finished`
        : ok === true
          ? "tool succeeded"
          : ok === false
            ? "tool failed"
            : "tool result"
      const detail =
        extractString(payload, ["summary", "message", "status"]) ||
        (artifactRef
          ? [
              `artifact ${artifactRef.kind}`,
              artifactRef.path ? `path=${artifactRef.path}` : "",
              artifactRef.sizeBytes != null ? `size=${artifactRef.sizeBytes}` : "",
            ]
              .filter(Boolean)
              .join(" ")
          : undefined) ||
        (toolResult === payload ? undefined : summarizeText(stableStringify(toolResult), 140))

      const normalized: NormalizedEvent = {
        ...base,
        type: "tool.result",
        actor: { kind: "tool" },
        toolCallId,
        toolName,
        toolClass,
        toolResult,
        artifactRef,
        ok,
        summary: {
          title: "Tool result",
          short,
          detail,
        },
        hints: deriveSlabDisplayHints("tool.result", { ok }),
      }
      return { ...normalized, toolRenderPolicy: deriveToolRenderPolicy(normalized) }
    }

    case "permission_request": {
      const requestId = extractString(payload, ["request_id", "requestId", "permission_id", "id"])
      const toolName = extractString(payload, ["tool", "tool_name", "command", "action"])
      return {
        ...base,
        type: "permission.request",
        actor: { kind: "system" },
        requestId,
        toolName,
        summary: {
          title: "Permission request",
          short: requestId ? `request ${requestId}` : "permission request",
          detail: toolName ? `for ${toolName}` : undefined,
        },
        hints: deriveSlabDisplayHints("permission.request"),
        overlayIntent: requestId
          ? {
              kind: "permission",
              action: "open",
              requestId,
            }
          : undefined,
      }
    }

    case "permission_response": {
      const requestId = extractString(payload, ["request_id", "requestId", "permission_id", "id"])
      const decision = normalizeDecision(extractString(payload, ["decision", "response", "outcome"]))
      const short = requestId
        ? decision
          ? `request ${requestId}: ${decision}`
          : `request ${requestId}: responded`
        : decision
          ? `permission ${decision}`
          : "permission response"

      return {
        ...base,
        type: "permission.response",
        actor: { kind: "system" },
        requestId,
        decision,
        summary: {
          title: "Permission response",
          short,
        },
        hints: deriveSlabDisplayHints("permission.response", { decision }),
        overlayIntent: requestId
          ? {
              kind: "permission",
              action: "close",
              requestId,
            }
          : undefined,
      }
    }

    case "task_event": {
      const taskId = extractString(payload, ["task_id", "taskId", "id"])
      const taskStatus = extractString(payload, ["status", "state"])
      const taskDescription = extractString(payload, ["description", "summary", "message"])
      const taskLaneId = extractString(payload, ["lane_id", "laneId"])
      const taskLaneLabel = extractString(payload, ["lane_label", "laneLabel", "lane_id", "laneId"])
      const childSessionId = extractString(payload, ["child_session_id", "childSessionId", "subagent_session_id", "subagentSessionId"])
      const parentSessionId = extractString(payload, ["parent_session_id", "parentSessionId"])
      const taskSubagentId = childSessionId ?? taskLaneId ?? taskLaneLabel
      const taskSubagentLabel =
        extractString(payload, ["child_session_label", "childSessionLabel", "subagent_label", "subagentLabel"]) ??
        taskLaneLabel ??
        taskSubagentId
      const short = [taskStatus, taskDescription].filter(Boolean).join(" · ") || "task update"
      return {
        ...base,
        type: "task.update",
        actor: { kind: "system" },
        taskId,
        taskStatus,
        taskDescription,
        taskLaneId,
        taskLaneLabel,
        childSessionId,
        parentSessionId,
        taskSubagentId,
        taskSubagentLabel,
        summary: {
          title: "Task update",
          short,
          detail: [
            taskId ? `task=${taskId}` : "",
            taskSubagentId ? `subagent=${taskSubagentId}` : "",
            taskSubagentLabel ? `label=${taskSubagentLabel}` : "",
          ]
            .filter(Boolean)
            .join(" "),
        },
        hints: deriveSlabDisplayHints("task.update", { decision: taskStatus }),
        overlayIntent: taskId
          ? {
              kind: "task",
              action: "update",
              taskId,
            }
          : undefined,
      }
    }

    case "warning": {
      const message = extractString(payload, ["message", "detail", "summary"]) || "warning"
      return {
        ...base,
        type: "warning",
        actor: { kind: "system" },
        summary: {
          title: "Warning",
          short: summarizeText(message, 120),
        },
        hints: deriveSlabDisplayHints("warning"),
      }
    }

    case "run_finished": {
      const completed = extractBoolean(payload, ["completed", "ok", "success"])
      const reason = extractString(payload, ["reason", "status", "message"])
      const eventCount = extractNumber(payload, ["eventCount", "event_count"])
      const statusLabel = completed === true ? "completed" : completed === false ? "stopped" : "finished"
      const detailParts: string[] = []
      if (reason) detailParts.push(`reason=${reason}`)
      if (eventCount !== undefined) detailParts.push(`events=${eventCount}`)

      return {
        ...base,
        type: "run.finished",
        actor: { kind: "system" },
        ok: completed,
        summary: {
          title: "Run finished",
          short: `run ${statusLabel}${reason ? `: ${reason}` : ""}`,
          detail: detailParts.length > 0 ? detailParts.join(" ") : undefined,
        },
        hints: deriveSlabDisplayHints("run.finished", { ok: completed }),
        overlayIntent: {
          kind: "task",
          action: "close",
        },
      }
    }

    case "error": {
      const message = extractString(payload, ["message", "detail", "summary", "error"]) || "unknown error"
      const code = extractString(payload, ["code", "error_code", "kind"])
      return {
        ...base,
        type: "error",
        actor: { kind: "system" },
        error: {
          message,
          code,
        },
        summary: {
          title: "Error",
          short: summarizeText(message, 120),
          detail: code ? `code=${code}` : undefined,
        },
        hints: deriveSlabDisplayHints("error"),
      }
    }

    default: {
      const summaryText =
        extractString(payload, ["summary", "message", "text", "reason", "status"]) ||
        (hasPayloadContent(payload) ? summarizeText(stableStringify(payload), 120) : "(no payload)")

      return {
        ...base,
        type: "unknown",
        actor: { kind: "system" },
        summary: {
          title: event.type,
          short: summaryText,
        },
        hints: deriveSlabDisplayHints("unknown", { rawType: event.type }),
      }
    }
  }
}

export const normalizeBridgeEvents = (events: readonly BridgeEvent[]): NormalizedEvent[] =>
  events.map((event) => normalizeBridgeEvent(event))
