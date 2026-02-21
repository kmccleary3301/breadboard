import { describe, expect, it, vi } from "vitest"
import { handleGlobalOverlayKeys } from "../handleGlobalOverlayKeys.js"

const KEY_DEFAULT = {
  ctrl: false,
  shift: false,
  meta: false,
  upArrow: false,
  downArrow: false,
  leftArrow: false,
  rightArrow: false,
  return: false,
  escape: false,
  tab: false,
  backspace: false,
  delete: false,
  pageUp: false,
  pageDown: false,
}

const makeInfo = (overrides?: Partial<any>) => ({
  char: "",
  key: {
    ...KEY_DEFAULT,
    ...(overrides?.key ?? {}),
  },
  lowerChar: undefined,
  isReturnKey: false,
  isTabKey: false,
  isShiftTab: false,
  isCtrlT: false,
  isCtrlShiftT: false,
  isCtrlB: false,
  isCtrlY: false,
  isCtrlG: false,
  isHomeKey: false,
  isEndKey: false,
  ...(overrides ?? {}),
})

const baseContext = () => ({
  clearScreen: vi.fn(),
  shortcutsOpen: false,
  shortcutsOpenedAtRef: { current: null as number | null },
  setShortcutsOpen: vi.fn(),
  usageOpen: false,
  setUsageOpen: vi.fn(),
  inspectMenu: { status: "hidden" },
  inspectRawMaxScroll: 0,
  inspectRawOpen: false,
  inspectRawViewportRows: 10,
  setInspectRawOpen: vi.fn(),
  setInspectRawScroll: vi.fn(),
  keymap: "claude",
  setCtreeOpen: vi.fn(),
  transcriptViewerOpen: false,
  enterTranscriptViewer: vi.fn(),
  exitTranscriptViewer: vi.fn(),
  setTodosOpen: undefined,
  setTasksOpen: undefined,
  setTaskFocusViewOpen: undefined,
  ctreeOpen: false,
  skillsMenu: { status: "hidden" },
  onSkillsMenuOpen: vi.fn(),
  onSkillsMenuCancel: vi.fn(),
  ctrlCPrimedAt: null as number | null,
  setCtrlCPrimedAt: vi.fn(),
  doubleCtrlWindowMs: 400,
  onSubmit: vi.fn(async () => {}),
  inputValueRef: { current: "" },
})

describe("handleGlobalOverlayKeys", () => {
  it("does not crash when todo setter is missing in claude ctrl+t path", () => {
    const context = baseContext()
    const handled = handleGlobalOverlayKeys(
      context,
      makeInfo({ key: { ctrl: true }, lowerChar: "t", isCtrlT: true }),
    )
    expect(handled).toBe(true)
    expect(context.setCtreeOpen).toHaveBeenCalledWith(false)
  })

  it("does not crash when task setter is missing in ctrl+b path", () => {
    const context = baseContext()
    const handled = handleGlobalOverlayKeys(
      context,
      makeInfo({ key: { ctrl: true }, lowerChar: "b", isCtrlB: true }),
    )
    expect(handled).toBe(true)
    expect(context.setCtreeOpen).toHaveBeenCalledWith(false)
  })

  it("closes optional overlays safely before opening ctree in ctrl+y path", () => {
    const context = baseContext()
    const handled = handleGlobalOverlayKeys(
      { ...context, ctreeOpen: false },
      makeInfo({ key: { ctrl: true }, lowerChar: "y", isCtrlY: true }),
    )
    expect(handled).toBe(true)
    expect(context.setCtreeOpen).toHaveBeenCalledWith(expect.any(Function))
  })
})
