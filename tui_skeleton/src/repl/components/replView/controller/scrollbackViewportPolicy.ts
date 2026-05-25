type ManagedViewportResetKeyInput = {
  readonly sessionId: string
  readonly viewClearAt?: number | null
  readonly networkBannerTone?: "error" | "warning" | null
  readonly hasGuardrailNotice: boolean
  readonly runStateEpoch?: string | number | null
  readonly composerResetToken?: string | number | null
}

type HistoryLandingAppendInput = {
  readonly scrollbackMode: boolean
  readonly hasLandingNode: boolean
  readonly landingRetired: boolean
  readonly landingShouldRetireNow: boolean
}


type ManagedBodyRowsInput = {
  readonly scrollbackMode: boolean
  readonly ownedSceneMode: boolean
  readonly staticFeedLineCount: number
  readonly guardrailRows: number
  readonly networkBannerRows: number
  readonly landingRows: number
  readonly sessionHeaderRows: number
  readonly ownedSceneHostRows: number
  readonly transcriptOrViewerRows: number
  readonly liveSlotRows: number
  readonly collapsedHintRows: number
  readonly overlayRows: number
}

type InlineSessionHeaderInput = {
  readonly scrollbackMode: boolean
  readonly hasSessionHeaderNode: boolean
  readonly landingRetired: boolean
  readonly warmLandingVisible: boolean
  readonly fitsActiveRegion?: boolean
}


export const computeManagedBodyRows = (input: ManagedBodyRowsInput): number => {
  const {
    scrollbackMode,
    ownedSceneMode,
    guardrailRows,
    networkBannerRows,
    landingRows,
    sessionHeaderRows,
    ownedSceneHostRows,
    transcriptOrViewerRows,
    liveSlotRows,
    collapsedHintRows,
    overlayRows,
  } = input
  if (!scrollbackMode && !ownedSceneMode) return 0

  // Static feed rows are already committed to terminal scrollback. They are
  // intentionally excluded from active managed-region reclaim; clearing them
  // on resize erases history that Ink Static will not repaint.
  let rows = 0
  rows += guardrailRows
  rows += networkBannerRows
  if (scrollbackMode) {
    rows += landingRows
    rows += sessionHeaderRows
  }
  if (ownedSceneMode) rows += ownedSceneHostRows
  rows += transcriptOrViewerRows
  rows += liveSlotRows
  rows += collapsedHintRows
  rows += overlayRows
  return Math.max(0, rows)
}

export const shouldAppendHistoryLanding = (input: HistoryLandingAppendInput): boolean => {
  const { scrollbackMode, hasLandingNode, landingRetired, landingShouldRetireNow } = input
  if (!scrollbackMode || !hasLandingNode) return false
  return landingRetired || landingShouldRetireNow
}

export const shouldShowInlineSessionHeader = (input: InlineSessionHeaderInput): boolean => {
  const { scrollbackMode, hasSessionHeaderNode, landingRetired, warmLandingVisible, fitsActiveRegion = true } = input
  if (!scrollbackMode || !hasSessionHeaderNode) return false
  if (warmLandingVisible) return false
  if (!fitsActiveRegion) return false
  return landingRetired
}

export const buildManagedViewportResetKey = (input: ManagedViewportResetKeyInput): string => {
  const { sessionId, viewClearAt, networkBannerTone, hasGuardrailNotice, runStateEpoch, composerResetToken } = input
  return [
    `session:${sessionId}`,
    `clear:${viewClearAt ?? 0}`,
    `banner:${networkBannerTone ?? "none"}`,
    `guardrail:${hasGuardrailNotice ? "on" : "off"}`,
    `composer:${composerResetToken ?? "none"}`,
    `run:${runStateEpoch ?? "stable"}`,
  ].join("|")
}
