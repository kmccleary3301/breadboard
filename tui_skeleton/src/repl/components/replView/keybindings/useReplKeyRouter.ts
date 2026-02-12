import { useCallback } from "react"
import { useKeyRouter, type KeyHandler } from "../../../hooks/useKeyRouter.js"
import { getTopLayer as getOverlayTopLayer, type OverlayFlags } from "./overlayState.js"

interface ReplKeyRouterOptions {
  readonly overlayFlags: OverlayFlags
  readonly modal: KeyHandler
  readonly editor: KeyHandler
  readonly palette: KeyHandler
  readonly global: KeyHandler
}

export const useReplKeyRouter = (options: ReplKeyRouterOptions) => {
  const { overlayFlags, modal, editor, palette, global } = options
  const getTopLayer = useCallback(() => getOverlayTopLayer(overlayFlags), [overlayFlags])
  useKeyRouter(getTopLayer, { modal, editor, palette, global })
  return { getTopLayer }
}
