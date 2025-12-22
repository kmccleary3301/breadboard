import React, { useEffect, useMemo, useRef } from "react"
import { Box, Text, useInput } from "ink"
import { enableBracketedPaste, disableBracketedPaste } from "../terminalControl.js"

const BRACKET_START = "\u001B[200~"
const BRACKET_END = "\u001B[201~"
const DEBUG_INPUT = false

const isWordCharacter = (char: string): boolean => /\w/.test(char)

const findPreviousWordBoundary = (value: string, cursor: number): number => {
  if (cursor <= 0) return 0
  let index = cursor - 1
  const targetIsWord = isWordCharacter(value[index])
  while (index > 0 && isWordCharacter(value[index - 1]) === targetIsWord && value[index - 1] !== " ") {
    index -= 1
  }
  while (index > 0 && value[index - 1] === " ") {
    index -= 1
  }
  return index
}

const findNextWordBoundary = (value: string, cursor: number): number => {
  if (cursor >= value.length) return value.length
  let index = cursor
  const targetIsWord = isWordCharacter(value[index])
  while (index < value.length && isWordCharacter(value[index]) === targetIsWord && value[index] !== " ") {
    index += 1
  }
  while (index < value.length && value[index] === " ") {
    index += 1
  }
  return index
}

export interface LineEditorProps {
  readonly value: string
  readonly cursor: number
  readonly focus: boolean
  readonly placeholder?: string
  readonly onChange: (value: string, cursor: number) => void
  readonly onSubmit: (value: string) => Promise<void> | void
}

export const LineEditor: React.FC<LineEditorProps> = ({ value, cursor, focus, placeholder, onChange, onSubmit }) => {
  const safeCursor = useMemo(() => Math.max(0, Math.min(cursor, value.length)), [cursor, value.length])
  const capturingPaste = useRef(false)
  const pasteBuffer = useRef("")

  const applyChange = (nextValue: string, nextCursor: number) => {
    onChange(nextValue, Math.max(0, Math.min(nextCursor, nextValue.length)))
  }

  const insertText = (text: string) => {
    if (!text) return
    const before = value.slice(0, safeCursor)
    const after = value.slice(safeCursor)
    const nextValue = `${before}${text}${after}`
    applyChange(nextValue, safeCursor + text.length)
  }

  const deleteBackward = () => {
    if (safeCursor === 0) return
    const before = value.slice(0, safeCursor - 1)
    const after = value.slice(safeCursor)
    applyChange(before + after, safeCursor - 1)
  }

  const deleteForward = () => {
    if (safeCursor >= value.length) return
    const before = value.slice(0, safeCursor)
    const after = value.slice(safeCursor + 1)
    applyChange(before + after, safeCursor)
  }

  const deleteToStart = () => {
    if (safeCursor === 0) return
    const after = value.slice(safeCursor)
    applyChange(after, 0)
  }

  const deleteToEnd = () => {
    if (safeCursor === value.length) return
    const before = value.slice(0, safeCursor)
    applyChange(before, safeCursor)
  }

  const deleteWordBackward = () => {
    const boundary = findPreviousWordBoundary(value, safeCursor)
    if (boundary === safeCursor) return
    const before = value.slice(0, boundary)
    const after = value.slice(safeCursor)
    applyChange(before + after, boundary)
  }

  const handlePastePayload = (payload: string) => {
    if (!payload) return
    insertText(payload)
  }

  useEffect(() => {
    if (focus) {
      enableBracketedPaste()
    }
    return () => disableBracketedPaste()
  }, [focus])

  useInput(
    (input, key) => {
    if (!focus) return

    if (capturingPaste.current) {
      pasteBuffer.current += input
      const endIndex = pasteBuffer.current.indexOf(BRACKET_END)
      if (endIndex >= 0) {
        const payload = pasteBuffer.current.slice(0, endIndex)
          capturingPaste.current = false
          pasteBuffer.current = ""
          handlePastePayload(payload)
        }
        return
      }

    if (input.length > 0) {
      const startIndex = input.indexOf(BRACKET_START)
      if (startIndex >= 0) {
        const afterStart = input.slice(startIndex + BRACKET_START.length)
        const endIndex = afterStart.indexOf(BRACKET_END)
        if (endIndex >= 0) {
          const payload = afterStart.slice(0, endIndex)
          handlePastePayload(payload)
        } else {
          capturingPaste.current = true
          pasteBuffer.current = afterStart
        }
        return
      }

      const chars = input.split("")
      let handledAll = true
      for (const char of chars) {
        if (char === "\r" || char === "\n") {
          void onSubmit(value)
          continue
        }
        if (char === "\u0017") {
          deleteWordBackward()
          continue
        }
        if (char === "\u0008" || char === "\u007f") {
          deleteBackward()
          continue
        }
        if (char >= " " && !key.ctrl && !key.meta) {
          insertText(char)
          continue
        }
        handledAll = false
      }
      if (handledAll) {
        return
      }
    }

    if (key.return) {
      void onSubmit(value)
      return
    }
    if (key.leftArrow) {
      applyChange(value, safeCursor - 1)
      return
    }
    if (key.rightArrow) {
      applyChange(value, safeCursor + 1)
      return
    }
    if (key.ctrl && input === "a") {
      applyChange(value, 0)
      return
    }
    if (key.ctrl && input === "e") {
      applyChange(value, value.length)
      return
    }
    if (key.ctrl && input === "u") {
      deleteToStart()
      return
    }
    if (key.ctrl && input === "k") {
      deleteToEnd()
      return
    }
    if (key.meta && input === "b") {
      applyChange(value, findPreviousWordBoundary(value, safeCursor))
      return
    }
    if (key.meta && input === "f") {
      applyChange(value, findNextWordBoundary(value, safeCursor))
      return
    }
    },
    { isActive: focus },
  )

  const before = value.slice(0, safeCursor)
  const caretChar = value[safeCursor] ?? " "
  const after = safeCursor < value.length ? value.slice(safeCursor + 1) : ""
  const isEmpty = value.length === 0

  if (isEmpty) {
    return (
      <Box>
        <Text inverse>{" "}</Text>
        {placeholder && (
          <Text color="gray">
            {" "}
            {placeholder}
          </Text>
        )}
      </Box>
    )
  }

  return (
    <Box>
      <Text>
        {before}
        <Text inverse>{caretChar}</Text>
        {after}
      </Text>
    </Box>
  )
}
            deleteBackward()
          }
        } else if (key.ctrl) {
          deleteWordForward()
        } else {
          deleteForward()
    const nextValue = `${before}${replacement}${after}`
    updateChipsAfterReplace(normalizedStart, normalizedEnd, replacement.length, options?.chip)
    applyChange(nextValue, normalizedStart + replacement.length)
    const afterState = snapshotState(normalizedStart + replacement.length)
    undoManager.current.push({ before: beforeState, after: afterState })
  }

  const insertText = (text: string) => {
    if (!text) return
    replaceRange(safeCursor, safeCursor, text)
  }

  const insertCollapsedPaste = (text: string) => {
    const token = pasteStore.current.put(text)
    const label = `[Pasted Content ${text.length} chars]`
    replaceRange(safeCursor, safeCursor, text, {
      chip: { id: token.id, label, color: PASTE_CHIP_COLOR, start: safeCursor, end: safeCursor + text.length },
    })
  }

  const deleteBackward = () => {
    if (safeCursor === 0) return
    const target = moveCursorLeft(safeCursor, chipsRef.current)
    replaceRange(target, safeCursor, "")
  }

  const deleteForward = () => {
    if (safeCursor >= valueRef.current.length) return
    const target = moveCursorRight(safeCursor, valueRef.current.length, chipsRef.current)
    replaceRange(safeCursor, target, "")
  }

  const deleteToStart = () => {
    if (safeCursor === 0) return
    replaceRange(0, safeCursor, "")
  }

  const deleteToEnd = () => {
    if (safeCursor === valueRef.current.length) return
    replaceRange(safeCursor, valueRef.current.length, "")
  }

  const deleteWordBackward = () => {
    const boundary = findPreviousWordBoundary(valueRef.current, safeCursor)
    replaceRange(boundary, safeCursor, "")
  }

  const deleteWordForward = () => {
    const boundary = findNextWordBoundary(valueRef.current, safeCursor)
    replaceRange(safeCursor, boundary, "")
  }

  const handlePastePayload = (payload: string) => {
    if (!payload) return
    if (payload.length >= PASTE_COLLAPSE_THRESHOLD) {
      insertCollapsedPaste(payload)
    } else {
      insertText(payload)
    }
  }

  const pasteFromClipboard = () => {
    if (clipboardJob.current) return
    clipboardJob.current = readClipboardContent()
      .then((payload) => {
        if (!payload) return
        if (payload.kind === "text") {
          handlePastePayload(payload.text)
          return
        }
        if (payload.kind === "image") {
          if (onPasteAttachment) {
            onPasteAttachment(payload)
          } else {
            insertText(attachmentPlaceholder(payload))
          }
        }
      })
      .catch((error) => {
        if (DEBUG_INPUT) {
          console.error(JSON.stringify({ clipboardError: String(error) }))
        }
      })
      .finally(() => {
        clipboardJob.current = null
      })
  }

  useEffect(() => {
    if (focus) {
      enableBracketedPaste()
    } else {
      resetPasteTracker(pasteTracker.current)
    }
    return () => disableBracketedPaste()
  }, [focus])

  useInput(
    (input, key) => {
      if (!focus) return

      if (DEBUG_INPUT) {
        console.error(JSON.stringify({ input, key }))
      }

      const normalizedInput = input.length === 1 ? input.toLowerCase() : ""
      const keyModifiers = { ctrl: key.ctrl, meta: key.meta }

      if (shouldTriggerClipboardPaste(keyModifiers, normalizedInput)) {
        pasteFromClipboard()
        return
      }

      if (key.backspace && !key.ctrl && !key.meta && input.length === 0) {
        deleteBackward()
        return
      }

      let handled = false
      if (!key.backspace && !key.delete) {
        handled = processInputChunk(input, pasteTracker.current, keyModifiers, {
          insertText,
          submit: () => {
            if (submitOnEnter) {
              void onSubmit(valueRef.current)
            }
          },
          deleteWordBackward,
          deleteBackward,
          handlePastePayload,
        })
        if (handled && input.length > 0) {
          return
        }
      }

      if (key.ctrl && !key.shift && normalizedInput === "z") {
        const snapshot = undoManager.current.undo()
        if (snapshot) restoreSnapshot(snapshot)
        return
      }
      if ((key.ctrl && key.shift && normalizedInput === "z") || (key.ctrl && normalizedInput === "y")) {
        const snapshot = undoManager.current.redo()
        if (snapshot) restoreSnapshot(snapshot)
        return
      }

      if (key.return) {
        if (submitOnEnter) {
          void onSubmit(valueRef.current)
        }
        return
      }
      if (key.ctrl && key.leftArrow) {
        applyChange(valueRef.current, moveCursorLeft(safeCursor, chipsRef.current))
        return
      }
      if (key.leftArrow) {
        applyChange(valueRef.current, moveCursorLeft(safeCursor, chipsRef.current))
        return
      }
      if (key.ctrl && key.rightArrow) {
        applyChange(valueRef.current, moveCursorRight(safeCursor, valueRef.current.length, chipsRef.current))
        return
      }
      if (key.rightArrow) {
        applyChange(valueRef.current, moveCursorRight(safeCursor, valueRef.current.length, chipsRef.current))
        return
      }
      if (key.backspace) {
        if (key.ctrl) {
          deleteWordBackward()
        } else {
          deleteBackward()
        }
        return
      }
      if (key.delete) {
        const treatAsBackspace = input.length === 0 && !key.ctrl
        if (key.ctrl && !treatAsBackspace) {
          deleteWordForward()
        } else if (treatAsBackspace) {
          deleteBackward()
        } else {
          deleteForward()
        }
        return
      }
      if (key.ctrl && normalizedInput === "a") {
        applyChange(valueRef.current, 0)
        return
      }
      if (key.ctrl && normalizedInput === "e") {
        applyChange(valueRef.current, valueRef.current.length)
        return
      }
      if (key.ctrl && normalizedInput === "u") {
        deleteToStart()
        return
      }
      if (key.ctrl && normalizedInput === "k") {
        deleteToEnd()
        return
      }
      if (key.meta && normalizedInput === "b") {
        applyChange(valueRef.current, moveCursorLeft(safeCursor, chipsRef.current))
        return
      }
      if (key.meta && normalizedInput === "f") {
        applyChange(valueRef.current, moveCursorRight(safeCursor, valueRef.current.length, chipsRef.current))
        return
      }
    },
    { isActive: focus },
  )

  const visualParts = useMemo(() => buildVisualParts(value, chips), [value, chips])
  const caretPosition = safeCursor

  if (value.length === 0 && chips.length === 0) {
    return (
      <Box>
        <Text inverse>{" "}</Text>
        {placeholder && (
          <Text color="gray">
            {" "}
            {placeholder}
          </Text>
        )}
      </Box>
    )
  }

  const renderedSegments: React.ReactNode[] = []
  let caretRendered = false

  const renderCaret = (char = " ") => {
    renderedSegments.push(
      <Text key={`caret-${renderedSegments.length}`} inverse>
        {char}
      </Text>,
    )
    caretRendered = true
  }

  for (const part of visualParts) {
    if (part.kind === "text" && part.text) {
      if (caretPosition >= part.start && caretPosition < part.end) {
        const relative = caretPosition - part.start
        const before = part.text.slice(0, relative)
        const caretChar = part.text[relative] ?? " "
        const after = part.text.slice(relative + 1)
        if (before.length > 0) renderedSegments.push(<Text key={`text-${part.start}`}>{before}</Text>)
        renderCaret(caretChar)
        if (after.length > 0) renderedSegments.push(<Text key={`text-${part.start}-tail`}>{after}</Text>)
      } else {
        renderedSegments.push(<Text key={`text-${part.start}`}>{part.text}</Text>)
      }
    } else if (part.kind === "chip" && part.label) {
      if (!caretRendered && caretPosition === part.start) {
        renderCaret()
      }
      renderedSegments.push(
        <Text key={`chip-${part.start}`} color={part.color}>
          {part.label}
        </Text>,
      )
      if (!caretRendered && caretPosition === part.end) {
        renderCaret()
      }
    }
  }

  if (!caretRendered) {
    renderCaret()
  }

  return <Box>{renderedSegments}</Box>
}
