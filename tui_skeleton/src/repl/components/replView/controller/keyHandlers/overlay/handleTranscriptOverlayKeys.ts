import type { OverlayHandlerContext, OverlayHandlerResult, OverlayKeyInfo } from "./types.js"

export const handleTranscriptOverlayKeys = (
  context: OverlayHandlerContext,
  info: OverlayKeyInfo,
): OverlayHandlerResult => {
  const {
    transcriptViewerOpen,
    exitTranscriptViewer,
    transcriptSearchMatches,
    setTranscriptSearchIndex,
    jumpTranscriptToLine,
    jumpTranscriptToAnchor,
    stdout,
    transcriptViewerFollowTail,
    transcriptViewerMaxScroll,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    transcriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptSearchOpen,
    saveTranscriptExport,
    transcriptToolLines,
    setTranscriptToolIndex,
    transcriptViewerBodyRows,
    transcriptViewerEffectiveScroll,
    resultDetailOpen,
    artifactPreviewOpen,
    selectedTranscriptToolTarget,
    activeTranscriptToolTarget,
    openSelectedTranscriptResultDetail,
    openSelectedTranscriptArtifactPreview,
    transcriptViewerInputQuarantineUntilRef,
  } = context
  const { char, key, lowerChar, isReturnKey, isTabKey, isShiftTab } = info
  const normalizedChar = typeof char === "string" && char.length === 1 ? char.toLowerCase() : undefined
  const effectiveLowerChar = lowerChar ?? normalizedChar

  if (!transcriptViewerOpen) return undefined
  if (resultDetailOpen || artifactPreviewOpen) return undefined

  if (transcriptSearchOpen && (key.escape || char === "\u001b")) {
    setTranscriptSearchOpen(false)
    return true
  }
  if (key.escape || char === "\u001b") {
    exitTranscriptViewer()
    return true
  }
  const quarantineUntil = Number(transcriptViewerInputQuarantineUntilRef?.current ?? 0)
  const isViewerOpenResidue =
    effectiveLowerChar === "/" ||
    effectiveLowerChar === "r" ||
    isReturnKey ||
    isTabKey
  if (quarantineUntil > Date.now() && isViewerOpenResidue && !key.ctrl && !key.meta) {
    return true
  }
  if ((key.ctrl && (effectiveLowerChar === "o" || effectiveLowerChar === "t")) || char === "\u000f" || char === "\u0014") {
    exitTranscriptViewer()
    return true
  }
  const cycleTranscriptMatch = (direction: -1 | 1) => {
    if (transcriptSearchMatches.length === 0) return
    setTranscriptSearchIndex((prev: number) => {
      const count = transcriptSearchMatches.length
      const next = count === 0 ? 0 : (prev + direction + count) % count
      const line = transcriptSearchMatches[next]?.line
      if (typeof line === "number") {
        jumpTranscriptToLine(line)
      }
      return next
    })
  }
  if (stdout?.isTTY && char && char.startsWith("[<")) {
    const match = char.match(/^\[<(\d+);(\d+);(\d+)([mM])$/)
    if (match) {
      const code = Number.parseInt(match[1] ?? "", 10)
      if (Number.isFinite(code) && (code & 64) === 64) {
        const down = (code & 1) === 1
        const delta = down ? 3 : -3
        setTranscriptViewerFollowTail(false)
        setTranscriptViewerScroll((prev: number) => {
          const base = transcriptViewerFollowTail ? transcriptViewerMaxScroll : prev
          return Math.max(0, Math.min(transcriptViewerMaxScroll, base + delta))
        })
        return true
      }
    }
  }
  if (transcriptSearchOpen) {
    if (effectiveLowerChar === "/") {
      setTranscriptSearchOpen(false)
      return true
    }
    if (isReturnKey || isTabKey) {
      if (transcriptSearchMatches.length > 0) {
        const direction = isShiftTab ? -1 : 1
        setTranscriptSearchIndex((prev: number) => {
          const count = transcriptSearchMatches.length
          const next = count === 0 ? 0 : (prev + direction + count) % count
          const line = transcriptSearchMatches[next]?.line
          if (typeof line === "number") {
            jumpTranscriptToLine(line)
          }
          return next
        })
      }
      return true
    }
    if (key.upArrow || key.downArrow) {
      if (transcriptSearchMatches.length > 0) {
        const direction = key.upArrow ? -1 : 1
        setTranscriptSearchIndex((prev: number) => {
          const count = transcriptSearchMatches.length
          const next = count === 0 ? 0 : (prev + direction + count) % count
          const line = transcriptSearchMatches[next]?.line
          if (typeof line === "number") {
            jumpTranscriptToLine(line)
          }
          return next
        })
      }
      return true
    }
    if (key.backspace || key.delete) {
      setTranscriptSearchQuery((prev: string) => prev.slice(0, -1))
      return true
    }
    if (key.ctrl && effectiveLowerChar === "u") {
      setTranscriptSearchQuery("")
      setTranscriptSearchIndex(0)
      return true
    }
    if (char && char.length > 0 && !key.ctrl && !key.meta) {
      setTranscriptSearchQuery((prev: string) => prev + char)
      return true
    }
  }
  if (!transcriptSearchOpen && effectiveLowerChar === "s" && !key.ctrl && !key.meta) {
    void saveTranscriptExport()
    return true
  }
  if (effectiveLowerChar === "n") {
    cycleTranscriptMatch(1)
    return true
  }
  if (effectiveLowerChar === "p") {
    cycleTranscriptMatch(-1)
    return true
  }
  if (effectiveLowerChar === "t" && !key.ctrl && !key.meta) {
    if (transcriptToolLines.length > 0) {
      const direction = key.shift ? -1 : 1
      setTranscriptToolIndex((prev: number) => {
        const count = transcriptToolLines.length
        const next = (prev + direction + count) % count
        const line = transcriptToolLines[next]
        if (typeof line === "number") {
          jumpTranscriptToLine(line)
        }
        return next
      })
    }
    return true
  }
  if (effectiveLowerChar === "o" && !key.ctrl && !key.meta) {
    return openSelectedTranscriptResultDetail()
  }
  const transcriptToolTarget = activeTranscriptToolTarget ?? selectedTranscriptToolTarget
  if (isReturnKey && transcriptToolTarget?.artifactPath) {
    return openSelectedTranscriptArtifactPreview()
  }
  if (effectiveLowerChar === "a" && !key.ctrl && !key.meta) {
    return jumpTranscriptToAnchor("assistant")
  }
  if (effectiveLowerChar === "u" && !key.ctrl && !key.meta) {
    return jumpTranscriptToAnchor("user")
  }
  if (effectiveLowerChar === "e" && !key.ctrl && !key.meta) {
    return jumpTranscriptToAnchor("error")
  }
  if (effectiveLowerChar === "w" && !key.ctrl && !key.meta) {
    return jumpTranscriptToAnchor("warning")
  }
  if (effectiveLowerChar === "/") {
    setTranscriptSearchOpen((prev: boolean) => !prev)
    return true
  }
  const scrollBy = (delta: number) => {
    setTranscriptViewerFollowTail(false)
    setTranscriptViewerScroll((prev: number) => {
      const base = transcriptViewerFollowTail ? transcriptViewerMaxScroll : prev
      return Math.max(0, Math.min(transcriptViewerMaxScroll, base + delta))
    })
  }
  if (key.pageUp || (key.ctrl && effectiveLowerChar === "b")) {
    scrollBy(-transcriptViewerBodyRows)
    return true
  }
  if (key.pageDown || (key.ctrl && effectiveLowerChar === "f")) {
    scrollBy(transcriptViewerBodyRows)
    return true
  }
  if (key.upArrow) {
    scrollBy(-1)
    return true
  }
  if (key.downArrow) {
    const next = Math.min(transcriptViewerMaxScroll, transcriptViewerEffectiveScroll + 1)
    if (next >= transcriptViewerMaxScroll) {
      setTranscriptViewerFollowTail(true)
      setTranscriptViewerScroll(transcriptViewerMaxScroll)
    } else {
      setTranscriptViewerFollowTail(false)
      setTranscriptViewerScroll(next)
    }
    return true
  }
  if (effectiveLowerChar === "g" && (key.shift || char === "G")) {
    setTranscriptViewerFollowTail(true)
    setTranscriptViewerScroll(transcriptViewerMaxScroll)
    return true
  }
  if (effectiveLowerChar === "g") {
    setTranscriptViewerFollowTail(false)
    setTranscriptViewerScroll(0)
    return true
  }
  return true
}
