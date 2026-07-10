import type { TranscriptItem } from "../../../transcriptModel.js"
import { sliceTailByLineBudget, type WindowSlice } from "../layout/windowing.js"

export interface ScrollbackSurfaceModel {
  readonly warmLandingVisible: boolean
  readonly coldCommitted: ReadonlyArray<TranscriptItem>
  readonly warmCommitted: ReadonlyArray<TranscriptItem>
  readonly activeWindow: WindowSlice<TranscriptItem>
  readonly transcriptBudgetRows: number
}

interface ComputeScrollbackSurfaceModelOptions {
  readonly committed: ReadonlyArray<TranscriptItem>
  readonly tail: ReadonlyArray<TranscriptItem>
  readonly bodyBudgetRows: number
  readonly landingRows: number
  readonly landingAlways: boolean
  readonly landingRetired?: boolean
  readonly pendingResponse?: boolean
  readonly preserveLatestSettledTurn?: boolean
  readonly measureTranscriptEntryLines: (entry: TranscriptItem) => number
}

const sumLines = (
  entries: ReadonlyArray<TranscriptItem>,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): number => entries.reduce((total, entry) => total + Math.max(1, measureTranscriptEntryLines(entry)), 0)

const clampActiveWindowToBudget = (
  items: ReadonlyArray<TranscriptItem>,
  totalTranscriptCount: number,
  transcriptBudgetRows: number,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): WindowSlice<TranscriptItem> => {
  const budget = Math.max(0, Math.floor(transcriptBudgetRows))
  if (items.length === 0 || budget <= 0) {
    return {
      items: [],
      hiddenCount: totalTranscriptCount,
      usedLines: 0,
      truncated: totalTranscriptCount > 0,
      budgetClipped: false,
    }
  }

  let nextItems = [...items]
  let hiddenCount = Math.max(0, totalTranscriptCount - nextItems.length)
  let usedLines = sumLines(nextItems, measureTranscriptEntryLines)
  let budgetClipped = false

  while (nextItems.length > 1 && usedLines > budget) {
    const [removed, ...rest] = nextItems
    nextItems = rest
    hiddenCount += 1
    usedLines = Math.max(0, usedLines - Math.max(1, measureTranscriptEntryLines(removed)))
  }

  if (nextItems.length === 1 && usedLines > budget) {
    const [entry] = nextItems
    nextItems = [{ ...entry, activePreviewLines: Math.max(1, budget) } as TranscriptItem]
    usedLines = sumLines(nextItems, measureTranscriptEntryLines)
    budgetClipped = true
  }

  return {
    items: nextItems,
    hiddenCount,
    usedLines,
    truncated: hiddenCount > 0 || budgetClipped,
    budgetClipped,
  }
}

type TranscriptGroup = {
  readonly items: ReadonlyArray<TranscriptItem>
  readonly lines: number
}

export const buildCommittedGroups = (
  committed: ReadonlyArray<TranscriptItem>,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): TranscriptGroup[] => {
  const groups: TranscriptGroup[] = []
  let current: TranscriptItem[] = []
  let seenUser = false
  const pushCurrent = (): void => {
    if (current.length === 0) return
    groups.push({
      items: current,
      lines: sumLines(current, measureTranscriptEntryLines),
    })
    current = []
  }
  for (const entry of committed) {
    const isUserMessage = entry.kind === "message" && entry.speaker === "user"
    if (isUserMessage) {
      pushCurrent()
      current.push(entry)
      seenUser = true
      continue
    }
    if (!seenUser) {
      groups.push({
        items: [entry],
        lines: Math.max(1, measureTranscriptEntryLines(entry)),
      })
      continue
    }
    current.push(entry)
  }
  pushCurrent()
  return groups
}

const buildSettledWarmWindow = (
  committed: ReadonlyArray<TranscriptItem>,
  transcriptBudgetRows: number,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): WindowSlice<TranscriptItem> => {
  const committedGroups = buildCommittedGroups(committed, measureTranscriptEntryLines)
  const latestGroup = committedGroups[committedGroups.length - 1]
  if (!latestGroup || transcriptBudgetRows <= 0) {
    return {
      items: [],
      hiddenCount: committed.length,
      usedLines: 0,
      truncated: committed.length > 0,
    }
  }

  const selectedGroups: TranscriptItem[][] = []
  let remaining = transcriptBudgetRows

  for (let index = committedGroups.length - 1; index >= 0; index -= 1) {
    const group = committedGroups[index]
    if (group.lines <= remaining) {
      selectedGroups.push([...group.items])
      remaining -= group.lines
      continue
    }
    if (selectedGroups.length === 0) {
      selectedGroups.push([...sliceTailByLineBudget(group.items, transcriptBudgetRows, measureTranscriptEntryLines).items])
    }
    break
  }

  let items = selectedGroups.reverse().flat()
  if (items.length === 1 && sumLines(items, measureTranscriptEntryLines) > transcriptBudgetRows) {
    const [entry] = items
    items = entry.kind === "message" && entry.speaker === "assistant"
      ? [{ ...entry, activePreviewLines: Math.max(1, transcriptBudgetRows) }]
      : items
  }
  return {
    items,
    hiddenCount: Math.max(0, committed.length - items.length),
    usedLines: sumLines(items, measureTranscriptEntryLines),
    truncated: items.length < committed.length,
  }
}

const buildActiveWindow = (
  committed: ReadonlyArray<TranscriptItem>,
  tail: ReadonlyArray<TranscriptItem>,
  pendingResponse: boolean,
  preserveLatestSettledTurn: boolean,
  transcriptBudgetRows: number,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): WindowSlice<TranscriptItem> => {
  const allTranscript = tail.length > 0 ? [...committed, ...tail] : [...committed]
  if (!pendingResponse && tail.length === 0) {
    return preserveLatestSettledTurn
      ? buildSettledWarmWindow(committed, transcriptBudgetRows, measureTranscriptEntryLines)
      : {
          items: [],
          // In preserved scrollback mode, settled transcript entries have
          // already moved into the static feed. An empty active repaint band is
          // therefore a clean handoff, not a visible truncation.
          hiddenCount: 0,
          usedLines: 0,
          truncated: false,
        }
  }

  const committedGroups = buildCommittedGroups(committed, measureTranscriptEntryLines)
  const tailLines = sumLines(tail, measureTranscriptEntryLines)
  const activeItems: TranscriptItem[] = []
  const selectedGroups: TranscriptItem[][] = []
  let nextGroupIndex = committedGroups.length - 1

  if (committedGroups.length > 0) {
    const latestCommittedGroup = committedGroups[committedGroups.length - 1]
    selectedGroups.push([...latestCommittedGroup.items])
    nextGroupIndex = committedGroups.length - 2
  }

  if (tail.length > 0) {
    const latestCommittedItems = selectedGroups.reverse().flat()
    const tailSlice = tailLines > transcriptBudgetRows
      ? sliceTailByLineBudget(tail, transcriptBudgetRows, measureTranscriptEntryLines)
      : { items: [...tail], usedLines: tailLines }
    const remainingCommittedBudget = Math.max(0, transcriptBudgetRows - tailSlice.usedLines)
    if (remainingCommittedBudget > 0 && latestCommittedItems.length > 0) {
      const committedSlice = sliceTailByLineBudget(latestCommittedItems, remainingCommittedBudget, measureTranscriptEntryLines)
      activeItems.push(...committedSlice.items, ...tailSlice.items)
    } else {
      activeItems.push(...tailSlice.items)
    }
  } else if (pendingResponse) {
    activeItems.push(...sliceTailByLineBudget(selectedGroups.reverse().flat(), transcriptBudgetRows, measureTranscriptEntryLines).items)
  } else {
    let remaining =
      transcriptBudgetRows - selectedGroups.reduce((total, group) => total + sumLines(group, measureTranscriptEntryLines), 0)
    for (let index = nextGroupIndex; index >= 0; index -= 1) {
      const group = committedGroups[index]
      if (group.lines <= remaining) {
        selectedGroups.push([...group.items])
        remaining -= group.lines
        continue
      }
      break
    }
    activeItems.push(...selectedGroups.reverse().flat())
  }

  return clampActiveWindowToBudget(activeItems, allTranscript.length, transcriptBudgetRows, measureTranscriptEntryLines)
}

export const computeScrollbackSurfaceModel = (
  options: ComputeScrollbackSurfaceModelOptions,
): ScrollbackSurfaceModel => {
  const {
    committed,
    tail,
    bodyBudgetRows,
    landingRows,
    landingAlways,
    landingRetired = false,
    pendingResponse = false,
    preserveLatestSettledTurn = false,
    measureTranscriptEntryLines,
  } = options

  const hasActiveTurn = pendingResponse || tail.length > 0
  const allTranscript = tail.length > 0 ? [...committed, ...tail] : [...committed]
  const totalTranscriptLines = sumLines(allTranscript, measureTranscriptEntryLines)
  const landingTranscriptBudgetRows = Math.max(0, bodyBudgetRows - landingRows)
  const landingCandidateWindow = hasActiveTurn
    ? buildActiveWindow(
        committed,
        tail,
        pendingResponse,
        false,
        landingTranscriptBudgetRows,
        measureTranscriptEntryLines,
      )
    : null
  const activeLandingFits =
    landingCandidateWindow != null &&
    landingTranscriptBudgetRows > 0 &&
    landingCandidateWindow.items.length > 0 &&
    landingCandidateWindow.usedLines <= landingTranscriptBudgetRows &&
    landingCandidateWindow.budgetClipped !== true
  const warmLandingVisible =
    !landingRetired &&
    !landingAlways &&
    landingRows > 0 &&
    bodyBudgetRows > 0 &&
    (hasActiveTurn ? activeLandingFits : totalTranscriptLines + landingRows <= bodyBudgetRows)

  const transcriptBudgetRows = Math.max(0, bodyBudgetRows - (warmLandingVisible ? landingRows : 0))
  const activeWindow = buildActiveWindow(
    committed,
    tail,
    pendingResponse,
    preserveLatestSettledTurn,
    transcriptBudgetRows,
    measureTranscriptEntryLines,
  )
  const activeIds = new Set(activeWindow.items.map((entry) => entry.id))
  const warmCommitted = committed.filter((entry) => activeIds.has(entry.id))
  const coldCommitted = committed.filter((entry) => !activeIds.has(entry.id))

  return {
    warmLandingVisible,
    coldCommitted,
    warmCommitted,
    activeWindow,
    transcriptBudgetRows,
  }
}
