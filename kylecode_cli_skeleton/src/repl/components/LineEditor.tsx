import React, { useEffect, useMemo, useRef, useState } from "react"
import { Box, Text, useInput } from "ink"
import { enableBracketedPaste, disableBracketedPaste } from "../terminalControl.js"
import {
  readClipboardContent,
  type ClipboardContent,
  type ClipboardImage,
} from "../../util/clipboard.js"
import {
  createPasteTracker,
  processInputChunk,
  resetPasteTracker,
  shouldTriggerClipboardPaste,
} from "../inputChunkProcessor.js"
import { HiddenPasteStore } from "../editor/hiddenPasteStore.js"
import { UndoManager } from "../editor/undoManager.js"
import { PASTE_COLLAPSE_THRESHOLD, type InputBufferState } from "../editor/types.js"

const DEBUG_INPUT = process.env.KYLECODE_INPUT_DEBUG === "1"
const PASTE_CHIP_COLOR = "#38BDF8"

interface ChipRange {
  id: string
  label: string
  color: string
  start: number
  end: number
}

interface VisualPart {
  kind: "text" | "chip"
  text?: string
  label?: string
  color?: string
  start: number
  end: number
}

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

const clampCursorToChips = (cursor: number, chips: ChipRange[], textLength: number): number => {
  let target = Math.max(0, Math.min(cursor, textLength))
  for (const chip of chips) {
    if (target > chip.start && target < chip.end) {
      const midpoint = chip.start + (chip.end - chip.start) / 2
      target = target < midpoint ? chip.start : chip.end
      break
    }
  }
  return target
}

const moveCursorLeft = (cursor: number, chips: ChipRange[]): number => {
  if (cursor <= 0) return 0
  for (const chip of chips) {
    if (cursor > chip.start && cursor <= chip.end) {
      return chip.start
    }
  }
  return cursor - 1
}

const moveCursorRight = (cursor: number, textLength: number, chips: ChipRange[]): number => {
  if (cursor >= textLength) return textLength
  for (const chip of chips) {
    if (cursor >= chip.start && cursor < chip.end) {
      return chip.end
    }
  }
  return Math.min(textLength, cursor + 1)
}

const buildVisualParts = (text: string, chips: ChipRange[]): VisualPart[] => {
  if (chips.length === 0) {
    return text.length === 0
      ? []
      : [{ kind: "text", text, start: 0, end: text.length }]
  }
  const parts: VisualPart[] = []
  const sorted = [...chips].sort((a, b) => a.start - b.start)
  let index = 0
  for (const chip of sorted) {
    if (chip.start > index) {
      parts.push({ kind: "text", text: text.slice(index, chip.start), start: index, end: chip.start })
    }
    parts.push({ kind: "chip", label: chip.label, color: chip.color, start: chip.start, end: chip.end })
    index = chip.end
  }
  if (index < text.length) {
    parts.push({ kind: "text", text: text.slice(index), start: index, end: text.length })
  }
  return parts
}

export interface LineEditorProps {
  readonly value: string
  readonly cursor: number
  readonly focus: boolean
  readonly placeholder?: string
  readonly onChange: (value: string, cursor: number) => void
  readonly onSubmit: (value: string) => Promise<void> | void
  readonly submitOnEnter?: boolean
  readonly onPasteAttachment?: (attachment: ClipboardImage) => void
}

const attachmentPlaceholder = (attachment: ClipboardImage) =>
  `[attachment ${attachment.mime} ${Math.round(attachment.size / 1024)}KB]`

export const LineEditor: React.FC<LineEditorProps> = ({
  value,
  cursor,
  focus,
  placeholder,
  onChange,
  onSubmit,
  submitOnEnter = true,
  onPasteAttachment,
}) => {
  const pasteTracker = useRef(createPasteTracker())
  const clipboardJob = useRef<Promise<void> | null>(null)
  const valueRef = useRef(value)
  const cursorRef = useRef(cursor)
  const chipsRef = useRef<ChipRange[]>([])
  const [chips, setChips] = useState<ChipRange[]>([])
  const pasteStore = useRef(new HiddenPasteStore())
  const undoManager = useRef(new UndoManager())
  const pendingLocalValue = useRef<string | null>(null)

  useEffect(() => {
    valueRef.current = value
    if (pendingLocalValue.current === value) {
      pendingLocalValue.current = null
      return
    }
    chipsRef.current = []
    setChips([])
    pasteStore.current.clear()
    undoManager.current.reset()
  }, [value])

  useEffect(() => {
    cursorRef.current = cursor
  }, [cursor])

  const safeCursor = useMemo(
    () => clampCursorToChips(Math.max(0, Math.min(cursor, value.length)), chips, value.length),
    [cursor, value.length, chips],
  )

  const applyChange = (nextValue: string, nextCursor: number) => {
    const normalizedCursor = clampCursorToChips(nextCursor, chipsRef.current, nextValue.length)
    pendingLocalValue.current = nextValue
    valueRef.current = nextValue
    cursorRef.current = normalizedCursor
    onChange(nextValue, normalizedCursor)
  }

  const getSafeCursor = () =>
    clampCursorToChips(
      Math.max(0, Math.min(cursorRef.current, valueRef.current.length)),
      chipsRef.current,
      valueRef.current.length,
    )

  const snapshotState = (cursorOverride = getSafeCursor()): InputBufferState => ({
    text: valueRef.current,
    cursor: clampCursorToChips(cursorOverride, chipsRef.current, valueRef.current.length),
    chips: chipsRef.current.map((chip) => ({
      id: chip.id,
      label: chip.label,
      color: chip.color,
      start: chip.start,
      end: chip.end,
    })),
    attachments: [],
  })

  const restoreSnapshot = (state: InputBufferState) => {
    const restoredChips: ChipRange[] = state.chips.map((chip) => ({
      id: chip.id,
      label: chip.label,
      color: chip.color ?? PASTE_CHIP_COLOR,
      start: chip.start,
      end: chip.end,
    }))
    chipsRef.current = restoredChips
    setChips(restoredChips)
    pasteStore.current.clear()
    for (const chip of restoredChips) {
      const segment = state.text.slice(chip.start, chip.end)
      pasteStore.current.restore(chip.id, segment)
    }
    applyChange(state.text, state.cursor)
  }

  const setChipState = (next: ChipRange[]) => {
    chipsRef.current = next
    setChips(next)
  }

  const updateChipsAfterReplace = (start: number, end: number, replacementLength: number, newChip?: ChipRange) => {
    const delta = replacementLength - (end - start)
    const next: ChipRange[] = []
    for (const chip of chipsRef.current) {
      if (chip.end <= start) {
        next.push(chip)
        continue
      }
      if (chip.start >= end) {
        next.push({ ...chip, start: chip.start + delta, end: chip.end + delta })
        continue
      }
      pasteStore.current.delete(chip.id)
    }
    if (newChip) {
      next.push({ ...newChip, start, end: start + replacementLength })
    }
    next.sort((a, b) => a.start - b.start)
    setChipState(next)
  }

  const replaceRange = (start: number, end: number, replacement: string, options?: { chip?: ChipRange }) => {
    const normalizedStart = Math.max(0, Math.min(start, valueRef.current.length))
    const normalizedEnd = Math.max(normalizedStart, Math.min(end, valueRef.current.length))
    const beforeState = snapshotState()
    const before = valueRef.current.slice(0, normalizedStart)
    const after = valueRef.current.slice(normalizedEnd)
    const nextValue = `${before}${replacement}${after}`
    updateChipsAfterReplace(normalizedStart, normalizedEnd, replacement.length, options?.chip)
    applyChange(nextValue, normalizedStart + replacement.length)
    const afterState = snapshotState(normalizedStart + replacement.length)
    undoManager.current.push({ before: beforeState, after: afterState })
  }

  const insertText = (text: string) => {
    if (!text) return
    const cursorNow = getSafeCursor()
    replaceRange(cursorNow, cursorNow, text)
  }

  const insertCollapsedPaste = (text: string) => {
    const token = pasteStore.current.put(text)
    const label = `[Pasted Content ${text.length} chars]`
    const cursorNow = getSafeCursor()
    replaceRange(cursorNow, cursorNow, text, {
      chip: { id: token.id, label, color: PASTE_CHIP_COLOR, start: cursorNow, end: cursorNow + text.length },
    })
  }

  const deleteBackward = () => {
    const cursorNow = getSafeCursor()
    if (cursorNow === 0) return
    const target = moveCursorLeft(cursorNow, chipsRef.current)
    replaceRange(target, cursorNow, "")
  }

  const deleteForward = () => {
    const cursorNow = getSafeCursor()
    if (cursorNow >= valueRef.current.length) return
    const target = moveCursorRight(cursorNow, valueRef.current.length, chipsRef.current)
    replaceRange(cursorNow, target, "")
  }

  const deleteToStart = () => {
    const cursorNow = getSafeCursor()
    if (cursorNow === 0) return
    replaceRange(0, cursorNow, "")
  }

  const deleteToEnd = () => {
    const cursorNow = getSafeCursor()
    if (cursorNow === valueRef.current.length) return
    replaceRange(cursorNow, valueRef.current.length, "")
  }

  const deleteWordBackward = () => {
    const cursorNow = getSafeCursor()
    const boundary = findPreviousWordBoundary(valueRef.current, cursorNow)
    replaceRange(boundary, cursorNow, "")
  }

  const deleteWordForward = () => {
    const cursorNow = getSafeCursor()
    const boundary = findNextWordBoundary(valueRef.current, cursorNow)
    replaceRange(cursorNow, boundary, "")
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

      if (key.escape && input.length === 0) {
        // Ink may surface a raw ESC byte as an escape key event (with empty input). Feed it to the
        // paste tracker so bracketed paste sequences can be reconstructed across chunk boundaries.
        processInputChunk("\u001b", pasteTracker.current, keyModifiers, {
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
        return
      }

      if (shouldTriggerClipboardPaste(keyModifiers, normalizedInput)) {
        pasteFromClipboard()
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
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorLeft(cursorNow, chipsRef.current))
        return
      }
      if (key.leftArrow) {
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorLeft(cursorNow, chipsRef.current))
        return
      }
      if (key.ctrl && key.rightArrow) {
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorRight(cursorNow, valueRef.current.length, chipsRef.current))
        return
      }
      if (key.rightArrow) {
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorRight(cursorNow, valueRef.current.length, chipsRef.current))
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
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorLeft(cursorNow, chipsRef.current))
        return
      }
      if (key.meta && normalizedInput === "f") {
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorRight(cursorNow, valueRef.current.length, chipsRef.current))
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
