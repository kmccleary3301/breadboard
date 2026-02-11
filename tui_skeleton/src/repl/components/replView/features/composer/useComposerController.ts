import { useCallback, useRef, useState } from "react"
import type { ClipboardImage } from "../../../../../util/clipboard.js"
import type { QueuedAttachment } from "../../../../types.js"

interface ComposerControllerOptions {
  readonly inputLocked: boolean
  readonly shortcutsOpen: boolean
  readonly setShortcutsOpen: (value: boolean) => void
}

export const useComposerController = (options: ComposerControllerOptions) => {
  const { inputLocked, shortcutsOpen, setShortcutsOpen } = options

  const [input, setInput] = useState("")
  const [cursor, setCursor] = useState(0)
  const [suggestIndex, setSuggestIndex] = useState(0)
  const [historyEntries, setHistoryEntries] = useState<string[]>([])
  const [historyPos, setHistoryPos] = useState(0)
  const historyDraftRef = useRef("")
  const [suppressSuggestions, setSuppressSuggestions] = useState(false)
  const [attachments, setAttachments] = useState<QueuedAttachment[]>([])
  const [inputTextVersion, setInputTextVersion] = useState(0)
  const inputValueRef = useRef("")

  const handleAttachment = useCallback((attachment: ClipboardImage) => {
    setAttachments((prev) => [
      ...prev,
      {
        id: `attachment-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        mime: attachment.mime,
        base64: attachment.base64,
        size: attachment.size,
      },
    ])
  }, [])

  const removeLastAttachment = useCallback(() => {
    setAttachments((prev) => prev.slice(0, -1))
  }, [])

  const clearAttachments = useCallback(() => {
    setAttachments([])
  }, [])

  const handleLineEdit = useCallback(
    (nextValue: string, nextCursor: number) => {
      const prevValue = inputValueRef.current
      inputValueRef.current = nextValue
      if (nextValue !== prevValue) {
        setInputTextVersion((prev) => prev + 1)
        if (suppressSuggestions) {
          setSuppressSuggestions(false)
        }
      }
      setInput(nextValue)
      setCursor(Math.max(0, Math.min(nextCursor, nextValue.length)))
      if (historyPos === historyEntries.length) {
        historyDraftRef.current = nextValue
      }
    },
    [historyEntries.length, historyPos, suppressSuggestions],
  )

  const handleLineEditGuarded = useCallback(
    (nextValue: string, nextCursor: number) => {
      if (inputLocked) return
      if (!shortcutsOpen && nextValue === "?" && inputValueRef.current.trim() === "") {
        setShortcutsOpen(true)
        handleLineEdit("", 0)
        return
      }
      handleLineEdit(nextValue, nextCursor)
    },
    [handleLineEdit, inputLocked, setShortcutsOpen, shortcutsOpen],
  )

  const pushHistoryEntry = useCallback((entry: string) => {
    if (!entry.trim()) return
    setHistoryEntries((prev) => {
      if (entry === prev[prev.length - 1]) {
        setHistoryPos(prev.length)
        return prev
      }
      const next = [...prev, entry]
      setHistoryPos(next.length)
      return next
    })
    historyDraftRef.current = ""
  }, [])

  const recallHistory = useCallback(
    (direction: -1 | 1) => {
      if (historyEntries.length === 0) return
      setHistoryPos((prev) => {
        const length = historyEntries.length
        let next = prev + direction
        next = Math.max(0, Math.min(length, next))
        if (next === prev) return prev
        if (prev === length) {
          historyDraftRef.current = input
        }
        if (next === length) {
          handleLineEdit(historyDraftRef.current, historyDraftRef.current.length)
        } else {
          const entry = historyEntries[next]
          handleLineEdit(entry, entry.length)
        }
        return next
      })
    },
    [handleLineEdit, historyEntries, input],
  )

  const moveCursorVertical = useCallback(
    (direction: -1 | 1) => {
      const text = inputValueRef.current
      if (!text.includes("\n")) return false
      const currentCursor = cursor
      const prevNewline = text.lastIndexOf("\n", Math.max(0, currentCursor - 1))
      const lineStart = prevNewline === -1 ? 0 : prevNewline + 1
      const column = currentCursor - lineStart
      if (direction === -1) {
        if (lineStart === 0) return false
        const prevLineEnd = lineStart - 1
        const prevLineStart = text.lastIndexOf("\n", Math.max(0, prevLineEnd - 1)) + 1
        const prevLineLength = prevLineEnd - prevLineStart
        const target = prevLineStart + Math.min(column, prevLineLength)
        handleLineEdit(text, target)
        return true
      }
      const nextNewline = text.indexOf("\n", currentCursor)
      if (nextNewline === -1) return false
      const nextLineStart = nextNewline + 1
      const nextLineEnd = text.indexOf("\n", nextLineStart)
      const nextLineLength = (nextLineEnd === -1 ? text.length : nextLineEnd) - nextLineStart
      const target = nextLineStart + Math.min(column, nextLineLength)
      handleLineEdit(text, target)
      return true
    },
    [cursor, handleLineEdit],
  )

  return {
    input,
    cursor,
    suggestIndex,
    setSuggestIndex,
    suppressSuggestions,
    setSuppressSuggestions,
    attachments,
    handleAttachment,
    removeLastAttachment,
    clearAttachments,
    inputTextVersion,
    inputValueRef,
    handleLineEdit,
    handleLineEditGuarded,
    pushHistoryEntry,
    recallHistory,
    moveCursorVertical,
  }
}
