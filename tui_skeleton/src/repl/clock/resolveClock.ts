import type {
  UIClock,
  UIClockAnimationListener,
  UIClockIntervalHandle,
  UIClockTimeoutHandle,
} from "./UIClock.js"
import { DEFAULT_SYSTEM_CLOCK } from "./systemClock.js"

export const parseUIClockMode = (value: string | undefined): "live" | "frozen" => {
  const mode = (value ?? "").trim().toLowerCase()
  if (mode === "frozen" || mode === "freeze" || mode === "capture") return "frozen"
  return "live"
}

export class FrozenUIClock implements UIClock {
  private readonly fixedNowMs: number

  constructor(seedNowMs?: number) {
    this.fixedNowMs =
      typeof seedNowMs === "number" && Number.isFinite(seedNowMs) ? Math.floor(seedNowMs) : DEFAULT_SYSTEM_CLOCK.now()
  }

  now(): number {
    return this.fixedNowMs
  }

  setTimeout(callback: () => void, delayMs: number): UIClockTimeoutHandle {
    return DEFAULT_SYSTEM_CLOCK.setTimeout(callback, delayMs)
  }

  clearTimeout(handle: UIClockTimeoutHandle | null | undefined): void {
    DEFAULT_SYSTEM_CLOCK.clearTimeout(handle)
  }

  setInterval(callback: () => void, delayMs: number): UIClockIntervalHandle {
    return DEFAULT_SYSTEM_CLOCK.setInterval(callback, delayMs)
  }

  clearInterval(handle: UIClockIntervalHandle | null | undefined): void {
    DEFAULT_SYSTEM_CLOCK.clearInterval(handle)
  }

  subscribeAnimationTick(_intervalMs: number, _listener: UIClockAnimationListener): () => void {
    return () => {}
  }
}

export const resolveUIClockFromEnv = (): UIClock => {
  const mode = parseUIClockMode(process.env.BREADBOARD_UI_CLOCK_MODE)
  if (mode === "frozen") {
    const fixedNow = Number(process.env.BREADBOARD_UI_CLOCK_FROZEN_NOW_MS)
    return new FrozenUIClock(Number.isFinite(fixedNow) ? fixedNow : undefined)
  }
  return DEFAULT_SYSTEM_CLOCK
}

