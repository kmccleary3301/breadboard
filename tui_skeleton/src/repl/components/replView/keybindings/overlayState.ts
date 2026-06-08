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
  readonly recentSessionsOpen: boolean
  readonly resultDetailOpen: boolean
  readonly artifactPreviewOpen: boolean
  readonly collapsedDetailOpen: boolean
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
  flags.transcriptViewerOpen ||
  flags.recentSessionsOpen ||
  flags.resultDetailOpen ||
  flags.artifactPreviewOpen ||
  flags.collapsedDetailOpen

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
  flags.transcriptViewerOpen ||
  flags.recentSessionsOpen ||
  flags.resultDetailOpen ||
  flags.artifactPreviewOpen ||
  flags.collapsedDetailOpen

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
    flags.transcriptViewerOpen ||
    flags.recentSessionsOpen ||
    flags.resultDetailOpen ||
    flags.artifactPreviewOpen ||
    flags.collapsedDetailOpen
  ) {
    return "modal"
  }
  if (flags.paletteOpen) return "palette"
  return "editor"
}

export const getOverlayFocusLabel = (flags: OverlayFlags): string | null => {
  if (flags.confirmOpen) return "Confirm"
  if (flags.permissionOpen) return "Permission"
  if (flags.modelMenuOpen) return "Models"
  if (flags.skillsMenuOpen) return "Skills"
  if (flags.inspectMenuOpen) return "Inspect"
  if (flags.usageOpen) return "Usage"
  if (flags.rewindOpen) return "Rewind"
  if (flags.todosOpen) return "Todos"
  if (flags.tasksOpen) return "Tasks"
  if (flags.ctreeOpen) return "Context Tree"
  if (flags.artifactPreviewOpen) return "Artifact"
  if (flags.resultDetailOpen) return "Result detail"
  if (flags.transcriptViewerOpen) return "Transcript"
  if (flags.recentSessionsOpen) return "Sessions"
  if (flags.collapsedDetailOpen) return "Detail"
  if (flags.paletteOpen) return "Palette"
  if (flags.shortcutsOpen && !flags.claudeChrome) return "Shortcuts"
  return null
}
