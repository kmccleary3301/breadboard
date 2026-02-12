import { useCallback, useRef, useState } from "react"

type PaletteState = { status: "hidden" | "open"; query: string; index: number }
type ConfirmState = {
  status: "hidden" | "prompt"
  message?: string
  action?: (() => Promise<void> | void) | null
}

export const useModalController = () => {
  const [paletteState, setPaletteState] = useState<PaletteState>({
    status: "hidden",
    query: "",
    index: 0,
  })
  const [confirmState, setConfirmState] = useState<ConfirmState>({ status: "hidden" })
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const shortcutsOpenedAtRef = useRef<number | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)

  const openPalette = useCallback(() => {
    setPaletteState({ status: "open", query: "", index: 0 })
  }, [])

  const closePalette = useCallback(() => {
    setPaletteState((prev) => (prev.status === "hidden" ? prev : { status: "hidden", query: "", index: 0 }))
  }, [])

  const openConfirm = useCallback((message: string, action: () => Promise<void> | void) => {
    setConfirmState({ status: "prompt", message, action })
  }, [])

  const closeConfirm = useCallback(() => {
    setConfirmState({ status: "hidden", message: undefined, action: null })
  }, [])

  const runConfirmAction = useCallback(() => {
    const action = confirmState.action
    closeConfirm()
    if (action) void action()
  }, [closeConfirm, confirmState.action])

  return {
    paletteState,
    setPaletteState,
    confirmState,
    openPalette,
    closePalette,
    openConfirm,
    closeConfirm,
    runConfirmAction,
    shortcutsOpen,
    setShortcutsOpen,
    shortcutsOpenedAtRef,
    usageOpen,
    setUsageOpen,
  }
}
