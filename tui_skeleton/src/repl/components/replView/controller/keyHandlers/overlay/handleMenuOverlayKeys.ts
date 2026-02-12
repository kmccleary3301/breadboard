import type { OverlayHandlerContext, OverlayHandlerResult, OverlayKeyInfo } from "./types.js"

export const handleMenuOverlayKeys = (
  context: OverlayHandlerContext,
  info: OverlayKeyInfo,
): OverlayHandlerResult => {
  const {
    skillsMenu,
    onSkillsMenuCancel,
    skillsData,
    applySkillsSelection,
    setSkillsSearch,
    setSkillsIndex,
    setSkillsOffset,
    skillsSearch,
    toggleSkillsMode,
    resetSkillsSelection,
    toggleSkillSelection,
    skillsSelectedEntry,
    skillsVisibleRows,
    modelMenu,
    onModelMenuCancel,
    filteredModels,
    modelIndex,
    onModelSelect,
    modelSearch,
    setModelSearch,
    setModelIndex,
    setModelOffset,
    modelProviderFilter,
    setModelProviderFilter,
    modelProviderOrder,
    normalizeProviderKey,
    modelVisibleRows,
  } = context
  const { char, key, lowerChar, isReturnKey, isTabKey, isShiftTab } = info

  if (skillsMenu.status !== "hidden") {
    if (key.escape || char === "\u001b") {
      onSkillsMenuCancel()
      return true
    }
    if (skillsMenu.status === "loading") {
      return true
    }
    if (skillsMenu.status === "error") {
      if (isReturnKey) onSkillsMenuCancel()
      return true
    }
    if (skillsMenu.status !== "ready") {
      return true
    }
    const count = skillsData.items.length
    if (isReturnKey) {
      void applySkillsSelection()
      return true
    }
    if (key.ctrl && lowerChar === "u") {
      setSkillsSearch("")
      setSkillsIndex(0)
      setSkillsOffset(0)
      return true
    }
    if (key.backspace || key.delete) {
      if (skillsSearch.length > 0) {
        setSkillsSearch((prev: string) => prev.slice(0, -1))
        setSkillsIndex(0)
        setSkillsOffset(0)
        return true
      }
    }
    if (!key.ctrl && !key.meta) {
      if (lowerChar === "m") {
        toggleSkillsMode()
        return true
      }
      if (lowerChar === "r") {
        resetSkillsSelection()
        return true
      }
      if (char === " " && skillsSelectedEntry) {
        toggleSkillSelection(skillsSelectedEntry)
        return true
      }
    }
    if (count > 0) {
      if (key.pageUp || key.pageDown) {
        const delta = key.pageUp ? -skillsVisibleRows : skillsVisibleRows
        setSkillsIndex((prev: number) => Math.max(0, Math.min(count - 1, prev + delta)))
        return true
      }
      if (key.downArrow || isTabKey) {
        setSkillsIndex((prev: number) => (prev + 1) % count)
        return true
      }
      if (key.upArrow || isShiftTab) {
        setSkillsIndex((prev: number) => (prev - 1 + count) % count)
        return true
      }
    }
    if (char && char.length > 0 && !key.ctrl && !key.meta && !key.return && !key.escape) {
      setSkillsSearch((prev: string) => prev + char)
      setSkillsIndex(0)
      setSkillsOffset(0)
      return true
    }
    return true
  }

  if (modelMenu.status === "hidden") return false
  if (key.escape) {
    onModelMenuCancel()
    return true
  }
  if (modelMenu.status === "loading") {
    return true
  }
  if (modelMenu.status === "error") {
    if (key.return) onModelMenuCancel()
    return true
  }
  if (modelMenu.status !== "ready") {
    return true
  }
  if (isReturnKey) {
    const choice = filteredModels[modelIndex]
    if (choice) void onModelSelect(choice)
    return true
  }
  if ((key.backspace || key.delete) && modelSearch.length > 0) {
    setModelSearch((prev: string) => prev.slice(0, -1))
    setModelIndex(0)
    setModelOffset(0)
    return true
  }
  if ((key.backspace || key.delete) && modelSearch.length === 0 && modelProviderFilter) {
    setModelProviderFilter(null)
    setModelIndex(0)
    setModelOffset(0)
    return true
  }
  const count = filteredModels.length
  if (count > 0) {
    if (key.leftArrow || key.rightArrow) {
      const providers = modelProviderOrder
      if (providers.length > 0) {
        if (!modelProviderFilter) {
          const currentKey = normalizeProviderKey(filteredModels[modelIndex]?.provider) ?? providers[0]
          setModelProviderFilter(currentKey)
        } else {
          const index = Math.max(0, providers.indexOf(modelProviderFilter))
          const nextIndex = (index + (key.rightArrow ? 1 : -1) + providers.length) % providers.length
          setModelProviderFilter(providers[nextIndex])
        }
        setModelIndex(0)
        setModelOffset(0)
      }
      return true
    }
    if (key.downArrow || isTabKey) {
      setModelIndex((index: number) => {
        const next = (index + 1) % count
        setModelOffset((offset: number) => {
          if (next < offset) return next
          if (next >= offset + modelVisibleRows) return Math.max(0, next - modelVisibleRows + 1)
          return offset
        })
        return next
      })
      return true
    }
    if (key.upArrow || isShiftTab) {
      setModelIndex((index: number) => {
        const next = (index - 1 + count) % count
        setModelOffset((offset: number) => {
          if (next < offset) return next
          if (next >= offset + modelVisibleRows) return Math.max(0, next - modelVisibleRows + 1)
          return offset
        })
        return next
      })
      return true
    }
    if (key.pageUp || key.pageDown) {
      const delta = key.pageUp ? -modelVisibleRows : modelVisibleRows
      setModelIndex((index: number) => {
        const next = Math.max(0, Math.min(count - 1, index + delta))
        setModelOffset((offset: number) => {
          if (next < offset) return next
          if (next >= offset + modelVisibleRows) return Math.max(0, next - modelVisibleRows + 1)
          return offset
        })
        return next
      })
      return true
    }
  }
  if (char && char.length > 0 && !key.meta && !key.ctrl) {
    setModelSearch((prev: string) => prev + char)
    setModelIndex(0)
    setModelOffset(0)
    return true
  }
  return true
}
