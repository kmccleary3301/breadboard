import { describe, expect, it } from "vitest"
import { getOverlayFocusLabel, type OverlayFlags } from "../overlayState.js"

const baseFlags = (): OverlayFlags => ({
  modelMenuOpen: false,
  skillsMenuOpen: false,
  inspectMenuOpen: false,
  paletteOpen: false,
  confirmOpen: false,
  shortcutsOpen: false,
  usageOpen: false,
  permissionOpen: false,
  rewindOpen: false,
  todosOpen: false,
  tasksOpen: false,
  ctreeOpen: false,
  transcriptViewerOpen: false,
  claudeChrome: false,
})

describe("getOverlayFocusLabel", () => {
  it("returns null when no overlays are open", () => {
    expect(getOverlayFocusLabel(baseFlags())).toBeNull()
  })

  it("prefers confirm when multiple overlays are open", () => {
    expect(
      getOverlayFocusLabel({
        ...baseFlags(),
        confirmOpen: true,
        tasksOpen: true,
        todosOpen: true,
      }),
    ).toBe("Confirm")
  })

  it("maps task and todo overlays to human-readable labels", () => {
    expect(getOverlayFocusLabel({ ...baseFlags(), tasksOpen: true })).toBe("Tasks")
    expect(getOverlayFocusLabel({ ...baseFlags(), todosOpen: true })).toBe("Todos")
  })

  it("suppresses shortcuts label in claude chrome mode", () => {
    expect(getOverlayFocusLabel({ ...baseFlags(), shortcutsOpen: true, claudeChrome: true })).toBeNull()
  })

  it("drops focus label when overlay closes", () => {
    const openFlags = { ...baseFlags(), tasksOpen: true }
    const closedFlags = { ...baseFlags(), tasksOpen: false }
    expect(getOverlayFocusLabel(openFlags)).toBe("Tasks")
    expect(getOverlayFocusLabel(closedFlags)).toBeNull()
  })

  it("re-targets focus label when active overlay switches", () => {
    const todoFlags = { ...baseFlags(), todosOpen: true }
    const taskFlags = { ...baseFlags(), tasksOpen: true }
    expect(getOverlayFocusLabel(todoFlags)).toBe("Todos")
    expect(getOverlayFocusLabel(taskFlags)).toBe("Tasks")
  })
})
