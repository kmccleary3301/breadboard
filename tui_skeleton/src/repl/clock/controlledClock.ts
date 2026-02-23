import type {
  UIClock,
  UIClockAnimationListener,
  UIClockIntervalHandle,
  UIClockTimeoutHandle,
} from "./UIClock.js"

interface ScheduledTimer {
  kind: "timeout" | "interval"
  dueAt: number
  intervalMs: number
  callback: () => void
}

interface AnimationBucket {
  tick: number
  timer: UIClockIntervalHandle | null
  listeners: Set<UIClockAnimationListener>
}

const normalizeDelayMs = (delayMs: number): number => {
  if (!Number.isFinite(delayMs)) return 0
  return Math.max(0, Math.floor(delayMs))
}

export class ControlledClock implements UIClock {
  private nowMs: number
  private nextTimerId = 1
  private readonly timers = new Map<number, ScheduledTimer>()
  private readonly animationBuckets = new Map<number, AnimationBucket>()

  constructor(seedNowMs = 0) {
    this.nowMs = Number.isFinite(seedNowMs) ? Math.floor(seedNowMs) : 0
  }

  now(): number {
    return this.nowMs
  }

  setNow(nowMs: number): void {
    if (!Number.isFinite(nowMs)) return
    this.nowMs = Math.floor(nowMs)
  }

  advance(stepMs: number): number {
    const delta = normalizeDelayMs(stepMs)
    const target = this.nowMs + delta
    while (true) {
      let nextId: number | null = null
      let nextDueAt = Number.POSITIVE_INFINITY
      for (const [id, timer] of this.timers) {
        if (timer.dueAt < nextDueAt) {
          nextDueAt = timer.dueAt
          nextId = id
        }
      }
      if (nextId == null || nextDueAt > target) break
      this.nowMs = nextDueAt
      const timer = this.timers.get(nextId)
      if (!timer) continue
      if (timer.kind === "timeout") {
        this.timers.delete(nextId)
      } else {
        timer.dueAt += Math.max(1, timer.intervalMs)
        this.timers.set(nextId, timer)
      }
      timer.callback()
    }
    this.nowMs = target
    return this.nowMs
  }

  setTimeout(callback: () => void, delayMs: number): UIClockTimeoutHandle {
    const id = this.nextTimerId++
    const dueAt = this.nowMs + normalizeDelayMs(delayMs)
    this.timers.set(id, {
      kind: "timeout",
      dueAt,
      intervalMs: 0,
      callback,
    })
    return id as unknown as UIClockTimeoutHandle
  }

  clearTimeout(handle: UIClockTimeoutHandle | null | undefined): void {
    if (handle == null) return
    this.timers.delete(Number(handle))
  }

  setInterval(callback: () => void, delayMs: number): UIClockIntervalHandle {
    const id = this.nextTimerId++
    const intervalMs = Math.max(1, normalizeDelayMs(delayMs))
    this.timers.set(id, {
      kind: "interval",
      dueAt: this.nowMs + intervalMs,
      intervalMs,
      callback,
    })
    return id as unknown as UIClockIntervalHandle
  }

  clearInterval(handle: UIClockIntervalHandle | null | undefined): void {
    if (handle == null) return
    this.timers.delete(Number(handle))
  }

  subscribeAnimationTick(intervalMs: number, listener: UIClockAnimationListener): () => void {
    const interval = Math.max(1, normalizeDelayMs(intervalMs))
    let bucket = this.animationBuckets.get(interval)
    if (!bucket) {
      bucket = {
        tick: 0,
        timer: null,
        listeners: new Set(),
      }
      this.animationBuckets.set(interval, bucket)
    }
    bucket.listeners.add(listener)
    if (bucket.timer == null) {
      bucket.timer = this.setInterval(() => {
        const current = this.animationBuckets.get(interval)
        if (!current) return
        current.tick += 1
        for (const callback of current.listeners) {
          callback(current.tick)
        }
      }, interval)
    }

    return () => {
      const current = this.animationBuckets.get(interval)
      if (!current) return
      current.listeners.delete(listener)
      if (current.listeners.size === 0) {
        this.clearInterval(current.timer)
        this.animationBuckets.delete(interval)
      }
    }
  }
}
