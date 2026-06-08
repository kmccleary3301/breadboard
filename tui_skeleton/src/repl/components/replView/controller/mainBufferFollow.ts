import type { TranscriptItem } from "../../../transcriptModel.js"
import type { ScrollbackSurfaceModel } from "./scrollbackSurfaceModel.js"

export interface MainBufferFollowSnapshot {
  readonly warmLandingVisible: boolean
  readonly activeWindowItems: ReadonlyArray<TranscriptItem>
}

const sumLines = (
  entries: ReadonlyArray<TranscriptItem>,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): number => entries.reduce((total, entry) => total + Math.max(1, measureTranscriptEntryLines(entry)), 0)

export const captureMainBufferFollowSnapshot = (
  model: ScrollbackSurfaceModel,
): MainBufferFollowSnapshot => ({
  warmLandingVisible: model.warmLandingVisible,
  activeWindowItems: [...model.activeWindow.items],
})

export const applyMainBufferFollowSnapshot = (
  model: ScrollbackSurfaceModel,
  snapshot: MainBufferFollowSnapshot,
  committed: ReadonlyArray<TranscriptItem>,
  tail: ReadonlyArray<TranscriptItem>,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): ScrollbackSurfaceModel => {
  const totalTranscript = tail.length > 0 ? [...committed, ...tail] : [...committed]
  const visibleIds = new Set(totalTranscript.map((entry) => entry.id))
  const activeWindowItems = snapshot.activeWindowItems.filter((entry) => visibleIds.has(entry.id))
  const hiddenCount = Math.max(0, totalTranscript.length - activeWindowItems.length)
  const truncated = hiddenCount > 0 && activeWindowItems.length > 0
  const usedLines = sumLines(activeWindowItems, measureTranscriptEntryLines) + (truncated ? 1 : 0)
  const activeIds = new Set(activeWindowItems.map((entry) => entry.id))
  return {
    ...model,
    warmLandingVisible: snapshot.warmLandingVisible,
    warmCommitted: committed.filter((entry) => activeIds.has(entry.id)),
    coldCommitted: committed.filter((entry) => !activeIds.has(entry.id)),
    activeWindow: {
      items: activeWindowItems,
      hiddenCount,
      usedLines,
      truncated,
    },
  }
}
