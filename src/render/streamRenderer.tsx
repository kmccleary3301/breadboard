import React, { useEffect, useState, useRef } from "react"
import { Box, Text } from "ink"
import type { SessionEvent } from "../api/types.js"

export interface StreamRendererProps {
  readonly events: SessionEvent[]
  readonly status?: string
}

export const StreamRenderer: React.FC<StreamRendererProps> = ({ events, status }) => {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [lines, setLines] = useState<string[]>([])

  useEffect(() => {
    const nextLines: string[] = []
    for (const event of events) {
      switch (event.type) {
        case "assistant_message": {
          const text = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
          nextLines.push(text)
          break
        }
        case "tool_call": {
          const payload = (event.payload ?? {}) as Record<string, any>
          const call = (payload.call ?? payload) as Record<string, any>
          const name = call?.function?.name ?? call?.name ?? "tool"
          nextLines.push(`[tool] ${name}`)
          break
        }
        case "tool_result": {
          nextLines.push(`[tool-result] ${JSON.stringify(event.payload)}`)
          break
        }
        case "reward_update": {
          nextLines.push(`[reward] ${JSON.stringify(event.payload)}`)
          break
        }
        case "error": {
          nextLines.push(`[error] ${JSON.stringify(event.payload)}`)
          break
        }
        default:
          break
      }
    }
    setLines(nextLines)
  }, [events])

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} paddingY={0} height={process.stdout.rows - 6}>
      <Box flexDirection="column">
        {lines.map((line, index) => (
          <Text key={`${index}-${line.slice(0, 16)}`}>{line}</Text>
        ))}
      </Box>
      {status && (
        <Box marginTop={1} borderStyle="single">
          <Text>{status}</Text>
        </Box>
      )}
    </Box>
  )
}
