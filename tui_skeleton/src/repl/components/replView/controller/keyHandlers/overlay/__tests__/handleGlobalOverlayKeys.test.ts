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
  todosOpen: false,
  setTodosOpen: undefined,
  tasksOpen: false,
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
  it("closes shortcuts overlay on escape", () => {
    const context = baseContext()
    const handled = handleGlobalOverlayKeys(
      { ...context, shortcutsOpen: true },
      makeInfo({ key: { escape: true } }),
    )
    expect(handled).toBe(true)
    expect(context.setShortcutsOpen).toHaveBeenCalledWith(false)
  })

  it("closes usage overlay on escape", () => {
    const context = baseContext()
    const handled = handleGlobalOverlayKeys(
      { ...context, usageOpen: true },
      makeInfo({ key: { escape: true } }),
    )
    expect(handled).toBe(true)
    expect(context.setUsageOpen).toHaveBeenCalledWith(false)
  })

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

  it("handles raw Ctrl+T control char for todos toggle", () => {
    const context = {
      ...baseContext(),
      setTodosOpen: vi.fn(),
    }
    const handled = handleGlobalOverlayKeys(
      context,
      makeInfo({ char: "\u0014" }),
    )
    expect(handled).toBe(true)
    expect(context.setTodosOpen).toHaveBeenCalledWith(true)
  })

  it("handles key.name fallback for Ctrl+B tasks toggle", () => {
    const context = {
      ...baseContext(),
      setTasksOpen: vi.fn(),
      setTaskFocusViewOpen: vi.fn(),
    }
    const handled = handleGlobalOverlayKeys(
      context,
      makeInfo({ key: { ctrl: true, name: "b" } }),
    )
    expect(handled).toBe(true)
    expect(context.setTasksOpen).toHaveBeenCalledWith(true)
  })

  it("closes todos panel when already open on ctrl+t", () => {
    const context = {
      ...baseContext(),
      todosOpen: true,
      setTodosOpen: vi.fn(),
    }
    const handled = handleGlobalOverlayKeys(
      context,
      makeInfo({ key: { ctrl: true, name: "t" } }),
    )
    expect(handled).toBe(true)
    expect(context.setTodosOpen).toHaveBeenCalledWith(false)
  })

  it("closes tasks panel when already open on ctrl+b", () => {
    const context = {
      ...baseContext(),
      tasksOpen: true,
      setTasksOpen: vi.fn(),
      setTaskFocusViewOpen: vi.fn(),
    }
    const handled = handleGlobalOverlayKeys(
      context,
      makeInfo({ key: { ctrl: true, name: "b" } }),
    )
    expect(handled).toBe(true)
    expect(context.setTasksOpen).toHaveBeenCalledWith(false)
  })
})
