import { useEffect, useMemo, useState } from "react"

import { resolveStdoutColumnDiagnostics, resolveStdoutRowDiagnostics } from "../inkScrollbackStdout.js"

export interface TerminalSizeSnapshot {
  readonly columns: number
  readonly rows: number
}

type TerminalSizeListener = () => void

const terminalSizeListeners = new Set<TerminalSizeListener>()
let terminalSizeInterval: NodeJS.Timeout | null = null

const emitTerminalSizeChange = () => {
  for (const listener of terminalSizeListeners) {
    listener()
  }
}

const startTerminalSizeSubscription = () => {
  if (terminalSizeInterval) return
  process.stdout?.on?.("resize", emitTerminalSizeChange)
  process.on?.("SIGWINCH", emitTerminalSizeChange)
  terminalSizeInterval = setInterval(emitTerminalSizeChange, 250)
  if (typeof terminalSizeInterval.unref === "function") terminalSizeInterval.unref()
}

const stopTerminalSizeSubscription = () => {
  if (!terminalSizeInterval) return
  clearInterval(terminalSizeInterval)
  terminalSizeInterval = null
  process.stdout?.off?.("resize", emitTerminalSizeChange)
  process.off?.("SIGWINCH", emitTerminalSizeChange)
}

export const subscribeTerminalSize = (listener: TerminalSizeListener): (() => void) => {
  terminalSizeListeners.add(listener)
  startTerminalSizeSubscription()
  return () => {
    terminalSizeListeners.delete(listener)
    if (terminalSizeListeners.size === 0) {
      stopTerminalSizeSubscription()
    }
  }
}

export const useTerminalSize = (stdout?: NodeJS.WriteStream | null): TerminalSizeSnapshot => {
  const [epoch, setEpoch] = useState(0)

  useEffect(() => {
    const bump = () => setEpoch((value) => value + 1)
    return subscribeTerminalSize(bump)
  }, [])

  return useMemo(() => {
    void epoch
    return {
      columns: resolveStdoutColumnDiagnostics(stdout?.columns).resolvedColumns,
      rows: resolveStdoutRowDiagnostics(stdout?.rows).resolvedRows,
    }
  }, [epoch, stdout?.columns, stdout?.rows])
}
