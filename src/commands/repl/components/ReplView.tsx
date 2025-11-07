import React, { useMemo, useState } from "react"
import { Box, Text, useInput } from "ink"
import TextInput from "ink-text-input"
import SelectInput from "ink-select-input"
import type { ConversationEntry, StreamStats, ModelMenuState, ModelMenuItem } from "../types.js"
import { StatusBar } from "./StatusBar.js"
import { buildSuggestions, SLASH_COMMAND_HINT } from "../slashCommands.js"
import { useHashTicker } from "../hooks/useHashTicker.js"

const MAX_SUGGESTIONS = 5

interface ReplViewProps {
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
}

const formatConversation = (entries: ConversationEntry[]): string[] =>
  entries.map((entry) => `${entry.speaker.toUpperCase()}: ${entry.text}`)

export const ReplView: React.FC<ReplViewProps> = ({
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
}) => {
  const [input, setInput] = useState("")
  const hashline = useHashTicker()
  const suggestions = useMemo(() => buildSuggestions(input, MAX_SUGGESTIONS), [input])
  const convoLines = useMemo(() => formatConversation(conversation), [conversation])
  const inputLocked = modelMenu.status !== "hidden"

  useInput((_, key) => {
    if (modelMenu.status === "ready" && key.escape) {
      onModelMenuCancel()
    }
  })

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text color="cyan">Session {sessionId}</Text>
      <StatusBar status={status} stats={stats} hashline={hashline} />
      <Text color="dim">Conversation</Text>
      {convoLines.length > 0 ? (
        <Box flexDirection="column" marginBottom={1}>
          {convoLines.map((line, index) => (
            <Text key={`convo-${index}`}>{line}</Text>
          ))}
        </Box>
      ) : (
        <Text color="gray">Waiting for assistant…</Text>
      )}
      <Text color="dim">Tool events</Text>
      {toolEvents.length > 0 ? (
        <Box flexDirection="column" marginBottom={1}>
          {toolEvents.map((line, index) => (
            <Text key={`tool-${index}`} color="blue">
              {line}
            </Text>
          ))}
        </Box>
      ) : (
        <Text color="gray">No tool activity yet.</Text>
      )}
      <Box marginTop={1} flexDirection="column">
        <TextInput
          value={input}
          focus={!inputLocked}
          placeholder="/help"
          onChange={setInput}
          onSubmit={async (value) => {
            if (!value.trim() || inputLocked) return
            await onSubmit(value)
            setInput("")
          }}
        />
        <Box marginTop={1} flexDirection="column">
          {suggestions.length > 0
            ? suggestions.map((row, index) => (
                <Text key={`suggestion-${index}`} color="dim">
                  {`${row.command}${row.usage ? ` ${row.usage}` : ""} — ${row.summary}`}
                </Text>
              ))
            : (
              <Text color="dim">{" "}</Text>
            )}
        </Box>
      </Box>
      <Box marginTop={1}>
        <Text color="dim">Slash commands: {SLASH_COMMAND_HINT}</Text>
      </Box>
      <Box>
        <Text color="dim">Press Tab to switch panes. Esc closes menus.</Text>
      </Box>
      {modelMenu.status !== "hidden" && (
        <Box marginTop={1} flexDirection="column" borderStyle="round" borderColor="green" padding={1}>
          {modelMenu.status === "loading" && <Text color="cyan">Loading model catalog…</Text>}
          {modelMenu.status === "error" && <Text color="red">{modelMenu.message}</Text>}
          {modelMenu.status === "ready" && (
            <>
              <Text color="green">Select a model (Enter to confirm, Esc to cancel)</Text>
              <SelectInput<ModelMenuItem>
                items={modelMenu.items.map((item) => ({ label: item.label, value: item, key: item.value }))}
                onSelect={(entry) => onModelSelect(entry.value)}
              />
            </>
          )}
        </Box>
      )}
      {hints.length > 0 && (
        <Box marginTop={1} flexDirection="column">
          {hints.slice(-4).map((hint, index) => (
            <Text key={`hint-${index}`} color="yellow">
              {hint}
            </Text>
          ))}
        </Box>
      )}
    </Box>
  )
}

