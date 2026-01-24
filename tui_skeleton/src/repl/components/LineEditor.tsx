import React, { useEffect, useMemo, useRef, useState } from "react"
import { Box, Text, useInput } from "ink"
import { enableBracketedPaste, disableBracketedPaste } from "../terminalControl.js"
import {
  readClipboardContent,
  type ClipboardContent,
  type ClipboardImage,
} from "../../util/clipboard.js"
import GraphemerModule from "graphemer"
import {
  createPasteTracker,
  processInputChunk,
  resetPasteTracker,
  shouldTriggerClipboardPaste,
} from "../inputChunkProcessor.js"
import { HiddenPasteStore } from "../editor/hiddenPasteStore.js"
import { UndoManager } from "../editor/undoManager.js"
import { PASTE_COLLAPSE_THRESHOLD, type InputBufferState } from "../editor/types.js"
import { SEMANTIC_COLORS } from "../designSystem.js"
import { SEMANTIC_COLORS } from "../designSystem.js"

const DEBUG_INPUT = process.env.BREADBOARD_INPUT_DEBUG === "1"
const PASTE_CHIP_COLOR = SEMANTIC_COLORS.info
type GraphemerInstance = {
  splitGraphemes?: (text: string) => string[]
  iterateGraphemes?: (text: string) => Iterable<string>
}

const GraphemerCtor = ((GraphemerModule as unknown as { default?: new () => GraphemerInstance }).default ??
  (GraphemerModule as unknown as new () => GraphemerInstance)) as new () => GraphemerInstance

let graphemer: GraphemerInstance | null = null
try {
  graphemer = new GraphemerCtor()
} catch {
  graphemer = null
}

const SegmenterCtor =
  (Intl as unknown as { Segmenter?: new (locales?: string | string[], options?: Record<string, unknown>) => any })
    .Segmenter ?? null
const segmenter = SegmenterCtor ? new SegmenterCtor(undefined, { granularity: "grapheme" }) : null

const graphemeBoundaries = (text: string): number[] => {
  if (text.length === 0) return [0]

  if (segmenter) {
    const boundaries = new Set<number>()
    boundaries.add(0)
    for (const seg of segmenter.segment(text)) {
      boundaries.add(seg.index)
    }
    boundaries.add(text.length)
    return Array.from(boundaries).sort((a, b) => a - b)
  }

  if (graphemer?.iterateGraphemes) {
    const boundaries: number[] = [0]
    let offset = 0
    for (const part of graphemer.iterateGraphemes(text)) {
      offset += part.length
      boundaries.push(offset)
    }
    if (boundaries[boundaries.length - 1] !== text.length) boundaries.push(text.length)
    return boundaries
  }

  if (graphemer?.splitGraphemes) {
    const boundaries: number[] = [0]
    let offset = 0
    for (const part of graphemer.splitGraphemes(text)) {
      offset += part.length
      boundaries.push(offset)
    }
    if (boundaries[boundaries.length - 1] !== text.length) boundaries.push(text.length)
    return boundaries
  }

  const fallback: number[] = []
  for (let index = 0; index <= text.length; index += 1) fallback.push(index)
  return fallback
}

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

const previousGraphemeBoundary = (text: string, index: number): number => {
  if (index <= 0) return 0
  const boundaries = graphemeBoundaries(text)
  let prev = 0
  for (const boundary of boundaries) {
    if (boundary >= index) break
    prev = boundary
  }
  return Math.max(0, Math.min(prev, index))
}

const nextGraphemeBoundary = (text: string, index: number): number => {
  if (index >= text.length) return text.length
  const boundaries = graphemeBoundaries(text)
  for (const boundary of boundaries) {
    if (boundary > index) return boundary
  }
  return text.length
}

const moveCursorLeft = (cursor: number, text: string, chips: ChipRange[]): number => {
  if (cursor <= 0) return 0
  for (const chip of chips) {
    if (cursor > chip.start && cursor <= chip.end) {
      return chip.start
    }
  }
  return previousGraphemeBoundary(text, cursor)
}

const moveCursorRight = (cursor: number, text: string, chips: ChipRange[]): number => {
  if (cursor >= text.length) return text.length
  for (const chip of chips) {
    if (cursor >= chip.start && cursor < chip.end) {
      return chip.end
    }
  }
  return nextGraphemeBoundary(text, cursor)
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
  readonly placeholderPad?: boolean
  readonly hideCaretWhenPlaceholder?: boolean
  readonly maxVisibleLines?: number
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
  placeholderPad = true,
  hideCaretWhenPlaceholder = false,
  maxVisibleLines,
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

  const renderState = useMemo(() => {
    const maxLines = maxVisibleLines && maxVisibleLines > 0 ? Math.floor(maxVisibleLines) : 0
    if (!maxLines || !value.includes("\n")) {
      return { text: value, chips, cursor: safeCursor }
    }
    const lines = value.split("\n")
    if (lines.length <= maxLines) {
      return { text: value, chips, cursor: safeCursor }
    }
    const lineStarts: number[] = [0]
    for (let i = 0; i < value.length; i += 1) {
      if (value[i] === "\n") lineStarts.push(i + 1)
    }
    let cursorLine = 0
    for (let i = 0; i < safeCursor; i += 1) {
      if (value[i] === "\n") cursorLine += 1
    }
    const half = Math.floor(maxLines / 2)
    let startLine = Math.max(0, cursorLine - half)
    startLine = Math.min(startLine, Math.max(0, lines.length - maxLines))
    const endLine = Math.min(lines.length, startLine + maxLines)
    const windowStart = lineStarts[startLine] ?? 0
    const windowEnd = endLine < lineStarts.length ? lineStarts[endLine] : value.length
    const windowText = value.slice(windowStart, windowEnd)
    const windowChips = chips
      .filter((chip) => chip.end > windowStart && chip.start < windowEnd)
      .map((chip) => ({
        ...chip,
        start: Math.max(0, chip.start - windowStart),
        end: Math.min(windowEnd - windowStart, chip.end - windowStart),
      }))
    const windowCursor = Math.max(0, Math.min(windowText.length, safeCursor - windowStart))
    return { text: windowText, chips: windowChips, cursor: windowCursor }
  }, [chips, maxVisibleLines, safeCursor, value])

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
    const target = moveCursorLeft(cursorNow, valueRef.current, chipsRef.current)
    replaceRange(target, cursorNow, "")
  }

  const deleteForward = () => {
    const cursorNow = getSafeCursor()
    if (cursorNow >= valueRef.current.length) return
    const target = moveCursorRight(cursorNow, valueRef.current, chipsRef.current)
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

  const moveWordBackward = () => {
    const cursorNow = getSafeCursor()
    const boundary = findPreviousWordBoundary(valueRef.current, cursorNow)
    applyChange(valueRef.current, boundary)
  }

  const moveWordForward = () => {
    const cursorNow = getSafeCursor()
    const boundary = findNextWordBoundary(valueRef.current, cursorNow)
    applyChange(valueRef.current, boundary)
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
      const keyExtras = key as typeof key & { home?: boolean; end?: boolean }
      const keyModifiers = { ctrl: key.ctrl, meta: key.meta }
      const isModifiedEnter = (() => {
        if (input.startsWith("\u001b[13;") && (input.endsWith("u") || input.endsWith("~"))) return true
        if (input.startsWith("\u001b[27;") && input.endsWith("~") && input.includes(";13")) return true
        return false
      })()

      if (key.ctrl && input === "\u001f") {
        const snapshot = undoManager.current.undo()
        if (snapshot) restoreSnapshot(snapshot)
        return
      }

      if (isModifiedEnter) {
        insertText("\n")
        return
      }

      if (key.return && (key.shift || (key.meta && !key.ctrl))) {
        insertText("\n")
        return
      }

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
      if (key.ctrl && normalizedInput === "a") {
        applyChange(valueRef.current, 0)
        return
      }
      if (key.ctrl && normalizedInput === "e") {
        applyChange(valueRef.current, valueRef.current.length)
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
        moveWordBackward()
        return
      }
      if (key.leftArrow) {
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorLeft(cursorNow, valueRef.current, chipsRef.current))
        return
      }
      if (key.ctrl && key.rightArrow) {
        moveWordForward()
        return
      }
      if (key.rightArrow) {
        const cursorNow = getSafeCursor()
        applyChange(valueRef.current, moveCursorRight(cursorNow, valueRef.current, chipsRef.current))
        return
      }
      if (keyExtras.home) {
        applyChange(valueRef.current, 0)
        return
      }
      if (keyExtras.end) {
        applyChange(valueRef.current, valueRef.current.length)
        return
      }
      if (key.backspace) {
        if (key.ctrl || key.meta) {
          deleteWordBackward()
        } else {
          deleteBackward()
        }
        return
      }
      if (key.delete) {
        const treatAsBackspace = input.length === 0 && !key.ctrl
        if ((key.ctrl || key.meta) && !treatAsBackspace) {
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
        moveWordBackward()
        return
      }
      if (key.meta && normalizedInput === "f") {
        moveWordForward()
        return
      }
      if (key.meta && key.leftArrow) {
        moveWordBackward()
        return
      }
      if (key.meta && key.rightArrow) {
        moveWordForward()
        return
      }
    },
    { isActive: focus },
  )

  const visualParts = useMemo(
    () => buildVisualParts(renderState.text, renderState.chips),
    [renderState.text, renderState.chips],
  )
  const caretPosition = renderState.cursor

  if (value.length === 0 && chips.length === 0) {
    if (placeholder && hideCaretWhenPlaceholder) {
      return (
        <Box>
          <Text color="gray">{placeholder}</Text>
        </Box>
      )
    }
    return (
      <Box>
        <Text inverse>{" "}</Text>
        {placeholder && (
          <Text color="gray">
            {placeholderPad ? " " : ""}
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
        if (before.length > 0) renderedSegments.push(<Text key={`text-${part.start}`}>{before}</Text>)
        if (caretChar === "\n") {
          renderCaret(" ")
          const remainder = part.text.slice(relative)
          if (remainder.length > 0) renderedSegments.push(<Text key={`text-${part.start}-tail`}>{remainder}</Text>)
        } else {
          const after = part.text.slice(relative + 1)
          renderCaret(caretChar)
          if (after.length > 0) renderedSegments.push(<Text key={`text-${part.start}-tail`}>{after}</Text>)
        }
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
