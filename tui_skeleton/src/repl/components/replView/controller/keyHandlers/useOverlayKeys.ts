import { useCallback } from "react"
import type { KeyHandler } from "../../../../hooks/useKeyRouter.js"
import { handleGlobalOverlayKeys } from "./overlay/handleGlobalOverlayKeys.js"
import { handleListOverlayKeys } from "./overlay/handleListOverlayKeys.js"
import { handleMenuOverlayKeys } from "./overlay/handleMenuOverlayKeys.js"
import { handlePermissionOverlayKeys } from "./overlay/handlePermissionOverlayKeys.js"
import { handleTranscriptOverlayKeys } from "./overlay/handleTranscriptOverlayKeys.js"
import type { OverlayHandlerContext, OverlayKeyInfo } from "./overlay/types.js"

export const useOverlayKeys = (context: OverlayHandlerContext): KeyHandler =>
  useCallback<KeyHandler>(
    function handleOverlayKeys(char, key): boolean {
      if (
        typeof char === "string" &&
        char.length > 1 &&
        !key.ctrl &&
        !key.meta &&
        !key.shift &&
        !key.tab &&
        !key.return &&
        !key.escape &&
        !key.backspace &&
        !key.delete &&
        !key.upArrow &&
        !key.downArrow &&
        !key.leftArrow &&
        !key.rightArrow &&
        !key.pageUp &&
        !key.pageDown &&
        !char.includes("\u001b")
      ) {
        let handled = false
        for (const ch of char) {
          handled = handleOverlayKeys(ch, key) || handled
        }
        return handled
      }
      const lowerChar = char?.toLowerCase()
      const isReturnKey = key.return || char === "\r" || char === "\n"
      const hasTabChar = typeof char === "string" && char.includes("\t")
      const hasShiftTabChar = typeof char === "string" && char.includes("\u001b[Z")
      const isTabKey = key.tab || hasTabChar || hasShiftTabChar
      const isShiftTab = (key.shift && isTabKey) || hasShiftTabChar
      const isCtrlT = key.ctrl && lowerChar === "t"
      const isCtrlShiftT = key.ctrl && key.shift && lowerChar === "t"
      const isCtrlB = key.ctrl && lowerChar === "b"
      const isCtrlY = (key.ctrl && lowerChar === "y") || char === "\u0019"
      const isCtrlG = (key.ctrl && lowerChar === "g") || char === "\u0007"
      const hasHomeChar = char === "\u001b[H" || char === "\u001b[1~" || char === "\u001bOH"
      const hasEndChar = char === "\u001b[F" || char === "\u001b[4~" || char === "\u001bOF"
      const isHomeKey = Boolean((key as Record<string, unknown>).home) || hasHomeChar
      const isEndKey = Boolean((key as Record<string, unknown>).end) || hasEndChar

      const info: OverlayKeyInfo = {
        char,
        key,
        lowerChar,
        isReturnKey,
        isTabKey,
        isShiftTab,
        isCtrlT,
        isCtrlShiftT,
        isCtrlB,
        isCtrlY,
        isCtrlG,
        isHomeKey,
        isEndKey,
      }

      const handlers = [
        handleGlobalOverlayKeys,
        handleTranscriptOverlayKeys,
        handlePermissionOverlayKeys,
        handleListOverlayKeys,
      ]
      for (const handler of handlers) {
        const result = handler(context, info)
        if (result !== undefined) return result
      }
      return handleMenuOverlayKeys(context, info) ?? false
    },
    [context],
  )
