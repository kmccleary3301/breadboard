import type {
  UIClock,
  UIClockAnimationListener,
  UIClockIntervalHandle,
  UIClockTimeoutHandle,
} from "./UIClock.js"

interface AnimationBucket {
  tick: number
  timer: UIClockIntervalHandle | null
  listeners: Set<UIClockAnimationListener>
}

const normalizeDelayMs = (delayMs: number): number => {
  if (!Number.isFinite(delayMs)) return 0
  return Math.max(0, Math.floor(delayMs))
}

export class SystemClock implements UIClock {
  private readonly animationBuckets = new Map<number, AnimationBucket>()

  now(): number {
    return Date.now()
  }

  setTimeout(callback: () => void, delayMs: number): UIClockTimeoutHandle {
    return setTimeout(callback, normalizeDelayMs(delayMs))
  }

  clearTimeout(handle: UIClockTimeoutHandle | null | undefined): void {
    if (handle == null) return
    clearTimeout(handle)
  }

  setInterval(callback: () => void, delayMs: number): UIClockIntervalHandle {
    return setInterval(callback, Math.max(1, normalizeDelayMs(delayMs)))
  }

  clearInterval(handle: UIClockIntervalHandle | null | undefined): void {
    if (handle == null) return
    clearInterval(handle)
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
        const nextTick = (bucket?.tick ?? 0) + 1
        if (!bucket) return
        bucket.tick = nextTick
        for (const callback of bucket.listeners) {
          callback(nextTick)
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

export const DEFAULT_SYSTEM_CLOCK = new SystemClock()

