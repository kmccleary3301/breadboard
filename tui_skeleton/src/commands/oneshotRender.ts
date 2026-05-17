import type { SessionEvent } from "../api/types.js"

type OutputStream = "stdout" | "stderr"

export type OneShotWrite = {
  stream: OutputStream
  text: string
}

type ToolState = {
  name: string
  execStarted: boolean
  stdout: string
  stderr: string
}

export type OneShotRenderState = {
  readonly showCommentary: boolean
  readonly toolCalls: Map<string, ToolState>
  mirrorCandidate: string | null
  mirrorBuffer: string
  mirrorActive: boolean
}

const resolvePayload = (event: SessionEvent): Record<string, unknown> =>
  event.payload && typeof event.payload === "object" ? (event.payload as Record<string, unknown>) : {}

const extractString = (record: Record<string, unknown>, keys: string[]): string | null => {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === "string" && value.trim().length > 0) return value
  }
  return null
}

const resolveToolName = (payload: Record<string, unknown>): string => {
  const call = payload.call && typeof payload.call === "object" ? (payload.call as Record<string, unknown>) : null
  const functionRecord =
    call?.function && typeof call.function === "object" ? (call.function as Record<string, unknown>) : null
  return (
    extractString(functionRecord ?? {}, ["name"]) ??
    extractString(call ?? {}, ["name", "tool", "tool_name"]) ??
    extractString(payload, ["tool_name", "tool", "name"]) ??
    "tool"
  )
}

const isShellLikeTool = (name: string): boolean => {
  const normalized = name.trim().toLowerCase()
  return normalized === "shell_command" || normalized === "run_shell"
}

const resolveCallId = (payload: Record<string, unknown>): string | null =>
  extractString(payload, ["call_id", "callId", "tool_call_id", "toolCallId", "id"])

const resolveErrorSummary = (payload: Record<string, unknown>): string | null => {
  const metadata =
    payload.metadata && typeof payload.metadata === "object" ? (payload.metadata as Record<string, unknown>) : null
  const exitCode = metadata?.exit_code ?? payload.exit_code ?? payload.exitCode
  if (typeof payload.message === "string" && payload.message.trim().length > 0) return payload.message.trim()
  if (typeof payload.error === "string" && payload.error.trim().length > 0) return payload.error.trim()
  if (typeof payload.status === "string" && payload.status.trim().length > 0) return payload.status.trim()
  if (typeof exitCode === "number") return `exit ${exitCode}`
  return null
}

const isSuccessfulToolResult = (payload: Record<string, unknown>): boolean => {
  if (payload.error === true) return false
  if (payload.success === false) return false
  const status = typeof payload.status === "string" ? payload.status.toLowerCase() : ""
  if (status.includes("error") || status.includes("fail")) return false
  if (status.includes("ok") || status.includes("success")) return true
  if (payload.success === true) return true
  return false
}

export const createOneShotRenderState = (options?: { showCommentary?: boolean }): OneShotRenderState => ({
  showCommentary: options?.showCommentary === true,
  toolCalls: new Map<string, ToolState>(),
  mirrorCandidate: null,
  mirrorBuffer: "",
  mirrorActive: false,
})

const clearMirror = (state: OneShotRenderState): void => {
  state.mirrorCandidate = null
  state.mirrorBuffer = ""
  state.mirrorActive = false
}

const beginMirrorCandidate = (state: OneShotRenderState, value: string | null): void => {
  if (!value) {
    clearMirror(state)
    return
  }
  state.mirrorCandidate = value
  state.mirrorBuffer = ""
  state.mirrorActive = true
}

const shouldMirrorToolStdout = (toolState: ToolState | null): string | null => {
  if (!toolState) return null
  const stdout = toolState.stdout
  const stderr = toolState.stderr
  if (!stdout || stderr.trim().length > 0) return null
  const normalized = stdout
  const trimmed = normalized.trimEnd()
  if (!trimmed || trimmed.includes("\n")) return null
  return normalized
}

const consumeAssistantDelta = (state: OneShotRenderState, delta: string): string => {
  if (!delta) return ""
  const candidate = state.mirrorCandidate
  if (!state.mirrorActive || !candidate) return delta
  const nextBuffer = state.mirrorBuffer + delta
  if (candidate.startsWith(nextBuffer)) {
    state.mirrorBuffer = nextBuffer
    return ""
  }
  if (nextBuffer.startsWith(candidate)) {
    state.mirrorBuffer = candidate
    state.mirrorActive = false
    return nextBuffer.slice(candidate.length)
  }
  clearMirror(state)
  return nextBuffer
}

const consumeAssistantMessage = (state: OneShotRenderState, text: string): string => {
  if (!text) return ""
  const candidate = state.mirrorCandidate
  if (!candidate) return text
  if (text === candidate || text === candidate.trimEnd()) {
    clearMirror(state)
    return ""
  }
  if (text.startsWith(candidate)) {
    clearMirror(state)
    return text.slice(candidate.length)
  }
  if (text.startsWith(candidate.trimEnd())) {
    clearMirror(state)
    return text.slice(candidate.trimEnd().length)
  }
  clearMirror(state)
  return text
}

export const renderOneShotEvent = (state: OneShotRenderState, event: SessionEvent): OneShotWrite[] => {
  const payload = resolvePayload(event)
  switch (event.type) {
    case "assistant.message.delta": {
      const delta = typeof payload.delta === "string" ? payload.delta : ""
      const text = consumeAssistantDelta(state, delta)
      return text ? [{ stream: "stdout", text }] : []
    }
    case "assistant_message": {
      const rawText = typeof payload.text === "string" ? payload.text : JSON.stringify(payload)
      if (!rawText || rawText === "None") return []
      const text = consumeAssistantMessage(state, rawText)
      return text ? [{ stream: "stdout", text: `${text}\n` }] : []
    }
    case "assistant.thought_summary.delta": {
      if (!state.showCommentary) return []
      const delta = typeof payload.delta === "string" ? payload.delta : ""
      return delta ? [{ stream: "stdout", text: `[thinking] ${delta}\n` }] : []
    }
    case "tool_call": {
      const name = resolveToolName(payload)
      const callId = resolveCallId(payload)
      if (callId) {
        state.toolCalls.set(callId, { name, execStarted: false, stdout: "", stderr: "" })
      }
      clearMirror(state)
      return isShellLikeTool(name) ? [] : [{ stream: "stdout", text: `[tool] ${name}\n` }]
    }
    case "tool.exec.start": {
      const name = resolveToolName(payload)
      const callId = resolveCallId(payload)
      if (callId) {
        const current = state.toolCalls.get(callId)
        state.toolCalls.set(callId, {
          name,
          execStarted: true,
          stdout: current?.stdout ?? "",
          stderr: current?.stderr ?? "",
        })
      }
      clearMirror(state)
      const command = typeof payload.command === "string" ? payload.command : name
      return [{ stream: "stdout", text: `$ ${command}\n` }]
    }
    case "tool.exec.stdout.delta": {
      const delta = typeof payload.delta === "string" ? payload.delta : ""
      const callId = resolveCallId(payload)
      if (callId) {
        const current = state.toolCalls.get(callId)
        if (current) {
          current.stdout += delta
          state.toolCalls.set(callId, current)
        }
      }
      return delta ? [{ stream: "stdout", text: delta }] : []
    }
    case "tool.exec.stderr.delta": {
      const delta = typeof payload.delta === "string" ? payload.delta : ""
      const callId = resolveCallId(payload)
      if (callId) {
        const current = state.toolCalls.get(callId)
        if (current) {
          current.stderr += delta
          state.toolCalls.set(callId, current)
        }
      }
      return delta ? [{ stream: "stderr", text: delta }] : []
    }
    case "tool_result": {
      const callId = resolveCallId(payload)
      const toolState = callId ? state.toolCalls.get(callId) ?? null : null
      if (callId) {
        state.toolCalls.delete(callId)
      }
      if (isSuccessfulToolResult(payload)) {
        beginMirrorCandidate(state, shouldMirrorToolStdout(toolState))
        return []
      }
      clearMirror(state)
      const toolName = toolState?.name ?? resolveToolName(payload)
      const summary = resolveErrorSummary(payload)
      return [{ stream: "stderr", text: `[tool error] ${toolName}${summary ? `: ${summary}` : ""}\n` }]
    }
    case "reward_update":
      return []
    case "error": {
      clearMirror(state)
      const message = typeof payload.message === "string" ? payload.message : JSON.stringify(payload)
      return message ? [{ stream: "stderr", text: `[error] ${message}\n` }] : []
    }
    default:
      return []
  }
}
