const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

export const formatBridgeEventForStdout = (event: Record<string, unknown>): string | null => {
  const type = typeof event.type === "string" ? event.type : ""
  const payload = isRecord(event.payload)
    ? event.payload
    : isRecord((event as any).data)
    ? ((event as any).data as Record<string, unknown>)
    : {}
  const readText = () => {
    const raw = (payload as any).text
    return typeof raw === "string" ? raw : ""
  }
  const readDelta = () => {
    const raw = (payload as any).delta ?? (payload as any).args_text_delta
    return typeof raw === "string" ? raw : ""
  }

  switch (type) {
    case "user.message": {
      const text = (payload as any).content ?? (payload as any).text
      return typeof text === "string" ? `\n[user]\n${text}\n` : null
    }
    case "user.command": {
      const cmd = (payload as any).command
      return typeof cmd === "string" ? `\n[user]\n/${cmd}\n` : null
    }
    case "assistant.message.start":
      return `\n[assistant]\n`
    case "assistant.message.delta": {
      const delta = readDelta()
      return delta ? delta : null
    }
    case "assistant.message.end":
      return `\n`
    case "assistant.reasoning.delta": {
      const delta = readDelta()
      return delta ? `\n[reasoning] ${delta}\n` : null
    }
    case "assistant.tool_call.start": {
      const name = (payload as any).tool_name ?? "tool_call"
      return `\n[tool] ${name}\n`
    }
    case "assistant.tool_call.delta": {
      const delta = readDelta()
      return delta ? delta : null
    }
    case "assistant.tool_call.end":
      return `\n`
    case "tool.exec.start": {
      const toolName = (payload as any).tool_name ?? "tool"
      const command = (payload as any).command ?? "running"
      return `\n[exec] ${toolName}: ${command}\n`
    }
    case "tool.exec.stdout.delta":
    case "tool.exec.stderr.delta": {
      const delta = readDelta()
      return delta ? delta : null
    }
    case "tool.exec.end": {
      const exitCode = (payload as any).exit_code
      return typeof exitCode === "number" ? `\n[exec] exit ${exitCode}\n` : `\n`
    }
    case "tool.result": {
      const ok = (payload as any).ok
      return `\n[tool] result${ok === false ? " (error)" : ""}\n`
    }
    case "permission.request":
      return `\n[permission] request\n`
    case "permission.decision":
      return `\n[permission] decision\n`
    case "permission.timeout":
      return `\n[permission] timeout\n`
    case "run.start":
      return `\n[run] start\n`
    case "run.end": {
      const reason = (payload as any).reason
      return `\n[run] end${typeof reason === "string" ? ` ${reason}` : ""}\n`
    }
    case "user_message": {
      const text = readText()
      return text ? `\n[user]\n${text}\n` : null
    }
    case "assistant_message": {
      const text = readText()
      return text ? `\n[assistant]\n${text}\n` : null
    }
    case "tool_call": {
      const call = (payload as any).call
      const name = call && typeof call.name === "string" ? call.name : "tool_call"
      return `\n[tool] ${name}\n`
    }
    case "tool_result": {
      const result = (payload as any).result
      const name = result && typeof result.name === "string" ? result.name : "tool_result"
      return `\n[tool] ${name} (result)\n`
    }
    case "permission_request":
      return `\n[permission] request\n`
    case "permission_response":
      return `\n[permission] response\n`
    case "error": {
      const message = (payload as any).message
      return `\n[error] ${typeof message === "string" ? message : "unknown"}\n`
    }
    case "log_link": {
      const url = (payload as any).url
      return typeof url === "string" ? `\n[log] ${url}\n` : null
    }
    case "run_finished": {
      const reason = (payload as any).reason
      return `\n[run_finished]${typeof reason === "string" ? ` ${reason}` : ""}\n`
    }
    default:
      return null
  }
}
