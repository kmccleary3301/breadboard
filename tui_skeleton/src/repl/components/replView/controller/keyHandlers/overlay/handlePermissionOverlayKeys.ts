import type { PermissionDecision } from "../../../../../types.js"
import type { OverlayHandlerContext, OverlayHandlerResult, OverlayKeyInfo } from "./types.js"

export const handlePermissionOverlayKeys = (
  context: OverlayHandlerContext,
  info: OverlayKeyInfo,
): OverlayHandlerResult => {
  const {
    permissionRequest,
    permissionTabRef,
    permissionTab,
    permissionDecisionTimerRef,
    permissionInputSnapshotRef,
    permissionNoteRef,
    permissionNote,
    permissionNoteCursor,
    setPermissionNote,
    setPermissionNoteCursor,
    setPermissionTab,
    setPermissionFileIndex,
    setPermissionScroll,
    permissionDiffSections,
    permissionViewportRows,
    permissionScope,
    alwaysAllowScope,
    onPermissionDecision,
    handleLineEdit,
  } = context
  const { char, key, lowerChar, isReturnKey, isTabKey, isShiftTab } = info

  if (!permissionRequest) return undefined

  const tabOrder: Array<"summary" | "diff" | "rules" | "note"> = ["summary", "diff", "rules", "note"]
  let activeTab = permissionTabRef.current ?? permissionTab
  const currentIndex = tabOrder.indexOf(activeTab as "summary" | "diff" | "rules")
  const nextTab = () => tabOrder[currentIndex >= 0 ? (currentIndex + 1) % tabOrder.length : 0] ?? "summary"
  const finalizePermissionDecision = (decision: PermissionDecision) => {
    if (permissionDecisionTimerRef.current) {
      clearTimeout(permissionDecisionTimerRef.current)
    }
    const snapshot = permissionInputSnapshotRef.current
    permissionDecisionTimerRef.current = setTimeout(() => {
      const latestNote = permissionNoteRef.current.trim()
      const notePayload = latestNote ? { note: latestNote } : {}
      void onPermissionDecision({ ...decision, ...notePayload })
      if (snapshot) {
        handleLineEdit(snapshot.value, snapshot.cursor)
      }
      permissionInputSnapshotRef.current = null
      if (permissionNoteRef.current) {
        setPermissionNote("")
        setPermissionNoteCursor(0)
      }
      permissionDecisionTimerRef.current = null
    }, 100)
  }

  if (isTabKey) {
    if (isShiftTab) {
      finalizePermissionDecision({
        kind: "allow-always",
        scope: alwaysAllowScope,
        rule: permissionRequest.ruleSuggestion ?? null,
      })
      return true
    }
    const next = nextTab()
    permissionTabRef.current = next
    setPermissionTab(next)
    return true
  }
  const isPrintable =
    char &&
    char.length > 0 &&
    !key.ctrl &&
    !key.meta &&
    !key.return &&
    !key.escape &&
    !key.backspace &&
    !key.delete
  if (
    isPrintable &&
    activeTab !== "note" &&
    lowerChar !== "a" &&
    lowerChar !== "d" &&
    lowerChar !== "p" &&
    lowerChar !== "1" &&
    lowerChar !== "2" &&
    lowerChar !== "3" &&
    char !== "D"
  ) {
    permissionTabRef.current = "note"
    setPermissionTab("note")
    activeTab = "note"
  }
  if (key.escape) {
    finalizePermissionDecision({ kind: "deny-stop" })
    return true
  }
  if (activeTab === "note") {
    if (isReturnKey) {
      finalizePermissionDecision({ kind: "deny-once" })
      return true
    }
    if (key.leftArrow) {
      setPermissionNoteCursor((prev: number) => Math.max(0, prev - 1))
      return true
    }
    if (key.rightArrow) {
      setPermissionNoteCursor((prev: number) => Math.min(permissionNote.length, prev + 1))
      return true
    }
    if (key.backspace) {
      if (permissionNoteCursor > 0) {
        const next = permissionNote.slice(0, permissionNoteCursor - 1) + permissionNote.slice(permissionNoteCursor)
        setPermissionNote(next)
        setPermissionNoteCursor(permissionNoteCursor - 1)
      }
      return true
    }
    if (key.delete) {
      if (permissionNoteCursor < permissionNote.length) {
        const next = permissionNote.slice(0, permissionNoteCursor) + permissionNote.slice(permissionNoteCursor + 1)
        setPermissionNote(next)
      }
      return true
    }
    if (key.ctrl && lowerChar === "u") {
      setPermissionNote("")
      setPermissionNoteCursor(0)
      return true
    }
    if (char && char.length > 0 && !key.ctrl && !key.meta) {
      const next = permissionNote.slice(0, permissionNoteCursor) + char + permissionNote.slice(permissionNoteCursor)
      setPermissionNote(next)
      setPermissionNoteCursor(permissionNoteCursor + char.length)
      return true
    }
    return true
  }
  if (lowerChar === "1") {
    finalizePermissionDecision({ kind: "allow-once" })
    return true
  }
  if (lowerChar === "2") {
    finalizePermissionDecision({
      kind: "allow-always",
      scope: alwaysAllowScope,
      rule: permissionRequest.ruleSuggestion ?? null,
    })
    return true
  }
  if (lowerChar === "3") {
    permissionTabRef.current = "note"
    setPermissionTab("note")
    return true
  }
  if (isReturnKey) {
    finalizePermissionDecision({ kind: "allow-once" })
    return true
  }
  if (lowerChar === "a") {
    finalizePermissionDecision({
      kind: "allow-always",
      scope: alwaysAllowScope,
      rule: permissionRequest.ruleSuggestion ?? null,
    })
    return true
  }
  if (char === "D") {
    finalizePermissionDecision({
      kind: "deny-always",
      scope: permissionScope,
      rule: permissionRequest.ruleSuggestion ?? null,
    })
    return true
  }
  if (lowerChar === "d") {
    finalizePermissionDecision({ kind: "deny-once" })
    return true
  }
  if (lowerChar === "p") {
    permissionTabRef.current = "diff"
    setPermissionTab("diff")
    return true
  }
  if (activeTab === "rules") {
    if (key.leftArrow || key.rightArrow) {
      return true
    }
    return true
  }
  if (activeTab === "diff") {
    const maxIndex = Math.max(0, permissionDiffSections.length - 1)
    if (key.leftArrow) {
      setPermissionFileIndex((prev: number) => Math.max(0, prev - 1))
      setPermissionScroll(0)
      return true
    }
    if (key.rightArrow) {
      setPermissionFileIndex((prev: number) => Math.min(maxIndex, prev + 1))
      setPermissionScroll(0)
      return true
    }
    if (key.pageUp) {
      setPermissionScroll((prev: number) => Math.max(0, prev - permissionViewportRows))
      return true
    }
    if (key.pageDown) {
      setPermissionScroll((prev: number) => prev + permissionViewportRows)
      return true
    }
    if (key.upArrow) {
      setPermissionScroll((prev: number) => Math.max(0, prev - 1))
      return true
    }
    if (key.downArrow) {
      setPermissionScroll((prev: number) => prev + 1)
      return true
    }
    return true
  }
  return true
}
