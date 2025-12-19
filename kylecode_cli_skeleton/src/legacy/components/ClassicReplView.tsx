import React from "react"
import { Box, Text, useInput } from "ink"
import TextInput from "ink-text-input"
import type { ConversationEntry, StreamStats, ModelMenuState, ModelMenuItem } from "../../repl/types.js"
import { ClassicStatusBar } from "./ClassicStatusBar.js"
import SelectInput from "ink-select-input"

interface ClassicReplViewProps {
  readonly sessionId: string
  readonly conversation: ConversationEntry[]
  readonly toolEvents: string[]
  readonly status: string
  readonly hints: string[]
  readonly stats: StreamStats
  readonly modelMenu: ModelMenuState
  readonly onSubmit: (value: string) => Promise<void>
  readonly onModelSelect: (item: ModelMenuItem) => Promise<void>
  readonly onModelMenuCancel: () => void
  readonly slashCommands: ReadonlyArray<{ readonly name: string; readonly summary: string; readonly usage?: string }>
}

const formatConversation = (entries: ConversationEntry[]): string[] =>
  entries.map((entry) => `${entry.speaker.toUpperCase()}: ${entry.text}`)

const buildSuggestions = (
  input: string,
  commands: ReadonlyArray<{ readonly name: string; readonly summary: string; readonly usage?: string }>,
  limit = 6,
) => {
  if (!input.startsWith("/")) return []
  const [needle] = input.slice(1).split(/\s+/)
  const normalized = needle.toLowerCase()
  return commands
    .filter((entry) => entry.name.startsWith(normalized))
    .slice(0, limit)
    .map((entry) => ({
      command: `/${entry.name}`,
      usage: entry.usage,
      summary: entry.summary,
    }))
}

export const ClassicReplView: React.FC<ClassicReplViewProps> = ({
  sessionId,
  conversation,
  toolEvents,
  status,
  hints,
  stats,
  modelMenu,
  onSubmit,
  onModelSelect,
  onModelMenuCancel,
  slashCommands,
}) => {
  const [input, setInput] = React.useState("")
  const convoLines = React.useMemo(() => formatConversation(conversation), [conversation])
  const slashHint = React.useMemo(
    () => slashCommands.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`).join(", "),
    [slashCommands],
  )
  const selectItems = React.useMemo(
    () =>
      modelMenu.status === "ready"
        ? modelMenu.items.map((item): { label: string; value: ModelMenuItem; key: string } => ({
            label: item.label,
            value: item,
            key: item.value,
          }))
        : [],
    [modelMenu],
  )

  useInput((_, key) => {
    if (modelMenu.status === "ready" && key.escape) {
      onModelMenuCancel()
    }
  })

  const suggestions = React.useMemo(() => buildSuggestions(input, slashCommands), [input, slashCommands])

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text color="cyan">{`Session ${sessionId}`}</Text>
      <ClassicStatusBar status={status} stats={stats} />

      <Box flexDirection="row">
        <Box flexDirection="column" flexGrow={1} borderStyle="double" borderColor="cyan" paddingX={1} paddingY={1}>
          <Text color="cyan">Conversation</Text>
          {convoLines.length === 0 ? (
            <Text color="gray">Waiting for assistant response…</Text>
          ) : (
            convoLines.map((line, idx) => (
              <Text key={`conv-${idx}`} wrap="truncate">
                {line}
              </Text>
            ))
          )}
        </Box>
        <Box width={2} />
        <Box flexDirection="column" width={40} borderStyle="double" borderColor="magenta" paddingX={1} paddingY={1}>
          <Text color="magenta">Tools &amp; Status</Text>
          {toolEvents.length === 0 ? (
            <Text color="gray">No tool activity yet.</Text>
          ) : (
            toolEvents.map((line: string, idx: number) => (
              <Text key={`tool-${idx}`} color="yellow" wrap="truncate">
                {line}
              </Text>
            ))
          )}
        </Box>
      </Box>

      <Box marginTop={1} flexDirection="column">
        <TextInput
          value={input}
          placeholder="/help"
          onChange={setInput}
          onSubmit={async (value) => {
            if (!value.trim()) return
            await onSubmit(value)
            setInput("")
          }}
        />
        <Box marginTop={1} flexDirection="column">
          {suggestions.length > 0 ? (
            suggestions.map((row, idx) => (
              <Text key={`suggestion-${idx}`} color="dim">
                {`${row.command}${row.usage ? ` ${row.usage}` : ""} — ${row.summary}`}
              </Text>
            ))
          ) : (
            <Text color="dim">{" "}</Text>
          )}
        </Box>
      </Box>

      <Box marginTop={1}>
        <Text color="dim">Slash commands: {slashHint}</Text>
      </Box>
      <Box>
        <Text color="dim">Press Tab to switch panes.</Text>
      </Box>

      {modelMenu.status !== "hidden" && (
        <Box marginTop={1} flexDirection="column" borderStyle="round" borderColor="green" padding={1}>
          {modelMenu.status === "loading" && <Text color="cyan">Loading model catalog…</Text>}
          {modelMenu.status === "error" && <Text color="red">{modelMenu.message}</Text>}
          {modelMenu.status === "ready" && (
            <>
              <Text color="green">Select a model</Text>
              <SelectInput<ModelMenuItem> items={selectItems} onSelect={(entry) => onModelSelect(entry.value)} />
            </>
          )}
        </Box>
      )}

      {hints.length > 0 && (
        <Box marginTop={1} flexDirection="column">
          {hints.slice(-4).map((hint, idx) => (
            <Text key={`hint-${idx}`} color="yellow">
              {hint}
            </Text>
          ))}
        </Box>
      )}
    </Box>
  )
}
