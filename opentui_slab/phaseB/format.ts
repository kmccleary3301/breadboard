const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

export const formatBridgeEventForStdout = (event: Record<string, unknown>): string | null => {
  const type = typeof event.type === "string" ? event.type : ""
  const payload = isRecord(event.payload) ? event.payload : {}
  const readText = () => {
    const raw = (payload as any).text
    return typeof raw === "string" ? raw : ""
  }

  switch (type) {
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

