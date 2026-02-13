import type { KeyHandler } from "../../../../../hooks/useKeyRouter.js"

export type OverlayHandlerContext = Record<string, any>

export type OverlayKeyInfo = {
  char?: string
  key: Parameters<KeyHandler>[1]
  lowerChar?: string
  isReturnKey: boolean
  isTabKey: boolean
  isShiftTab: boolean
  isCtrlT: boolean
  isCtrlShiftT: boolean
  isCtrlB: boolean
  isCtrlY: boolean
  isCtrlG: boolean
  isHomeKey: boolean
  isEndKey: boolean
}

export type OverlayHandlerResult = boolean | undefined
