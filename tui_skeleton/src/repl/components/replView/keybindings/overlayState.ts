import type { LayerName } from "../../../hooks/useKeyRouter.js"

export interface OverlayFlags {
  readonly modelMenuOpen: boolean
  readonly skillsMenuOpen: boolean
  readonly inspectMenuOpen: boolean
  readonly paletteOpen: boolean
  readonly confirmOpen: boolean
  readonly shortcutsOpen: boolean
  readonly usageOpen: boolean
  readonly permissionOpen: boolean
  readonly rewindOpen: boolean
  readonly todosOpen: boolean
  readonly tasksOpen: boolean
  readonly ctreeOpen: boolean
  readonly transcriptViewerOpen: boolean
  readonly claudeChrome: boolean
}

export const computeInputLocked = (flags: OverlayFlags): boolean =>
  flags.modelMenuOpen ||
  flags.skillsMenuOpen ||
  flags.inspectMenuOpen ||
  flags.paletteOpen ||
  flags.confirmOpen ||
  flags.shortcutsOpen ||
  flags.usageOpen ||
  flags.permissionOpen ||
  flags.rewindOpen ||
  flags.todosOpen ||
  flags.tasksOpen ||
  flags.ctreeOpen ||
  flags.transcriptViewerOpen

export const computeOverlayActive = (flags: OverlayFlags): boolean =>
  flags.modelMenuOpen ||
  flags.skillsMenuOpen ||
  flags.inspectMenuOpen ||
  flags.paletteOpen ||
  flags.confirmOpen ||
  (flags.shortcutsOpen && !flags.claudeChrome) ||
  flags.usageOpen ||
  flags.permissionOpen ||
  flags.rewindOpen ||
  flags.todosOpen ||
  flags.tasksOpen ||
  flags.ctreeOpen ||
  flags.transcriptViewerOpen

export const getTopLayer = (flags: OverlayFlags): LayerName => {
  if (
    flags.confirmOpen ||
    flags.modelMenuOpen ||
    flags.skillsMenuOpen ||
    flags.inspectMenuOpen ||
    flags.shortcutsOpen ||
    flags.usageOpen ||
    flags.permissionOpen ||
    flags.rewindOpen ||
    flags.todosOpen ||
    flags.tasksOpen ||
    flags.ctreeOpen ||
    flags.transcriptViewerOpen
  ) {
    return "modal"
  }
  if (flags.paletteOpen) return "palette"
  return "editor"
}
