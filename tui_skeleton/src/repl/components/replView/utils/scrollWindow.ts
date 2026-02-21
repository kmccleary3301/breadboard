export interface ScrollWindow<T> {
  readonly total: number
  readonly viewport: number
  readonly maxScroll: number
  readonly scroll: number
  readonly start: number
  readonly end: number
  readonly visible: ReadonlyArray<T>
}

export const createScrollWindow = <T>(
  rows: ReadonlyArray<T>,
  requestedScroll: number,
  requestedViewport: number,
): ScrollWindow<T> => {
  const total = rows.length
  const viewport = Math.max(1, Math.floor(Number.isFinite(requestedViewport) ? requestedViewport : 1))
  const maxScroll = Math.max(0, total - viewport)
  const boundedScroll = Number.isFinite(requestedScroll) ? Math.floor(requestedScroll) : 0
  const scroll = Math.max(0, Math.min(boundedScroll, maxScroll))
  const start = scroll
  const end = Math.min(total, start + viewport)
  const visible = rows.slice(start, end)
  return {
    total,
    viewport,
    maxScroll,
    scroll,
    start,
    end,
    visible,
  }
}

export const formatScrollRange = (
  window: ScrollWindow<unknown>,
  options?: { includeScrollOffset?: boolean },
): string => {
  if (window.total <= 0) return "0-0 of 0"
  const base = `${window.start + 1}-${window.end} of ${window.total}`
  if (options?.includeScrollOffset !== true || window.maxScroll <= 0) return base
  return `${base} • scroll ${window.start}/${window.maxScroll}`
}
