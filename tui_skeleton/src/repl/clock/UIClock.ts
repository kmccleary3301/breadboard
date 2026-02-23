export type UIClockTimeoutHandle = ReturnType<typeof setTimeout>
export type UIClockIntervalHandle = ReturnType<typeof setInterval>

export type UIClockAnimationListener = (tick: number) => void

export interface UIClock {
  now(): number
  setTimeout(callback: () => void, delayMs: number): UIClockTimeoutHandle
  clearTimeout(handle: UIClockTimeoutHandle | null | undefined): void
  setInterval(callback: () => void, delayMs: number): UIClockIntervalHandle
  clearInterval(handle: UIClockIntervalHandle | null | undefined): void
  subscribeAnimationTick(intervalMs: number, listener: UIClockAnimationListener): () => void
}

