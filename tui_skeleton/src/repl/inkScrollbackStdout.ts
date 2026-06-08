const DESTRUCTIVE_CLEAR_PATTERN = /\x1b\[2J\x1b\[3J\x1b\[H|\x1b\[3J|\x1bc/g
const SCROLLBACK_SAFE_VIEWPORT_RECLAIM = "\r\x1b[J"
const INK_SCROLLBACK_SAFE_ROWS = 100_000

export const normalizeScrollbackSafeInkWrite = (value: string): string =>
  value.replace(DESTRUCTIVE_CLEAR_PATTERN, SCROLLBACK_SAFE_VIEWPORT_RECLAIM)

export const createScrollbackSafeInkStdout = (
  stdout: NodeJS.WriteStream,
  enabled: boolean,
): NodeJS.WriteStream => {
  if (!enabled) return stdout
  return new Proxy(stdout, {
    get(target, property, receiver) {
      if (property === "rows") return Math.max(Number(target.rows ?? 0), INK_SCROLLBACK_SAFE_ROWS)
      const value = Reflect.get(target, property, receiver)
      if (property === "write" && typeof value === "function") {
        return (chunk: unknown, ...args: unknown[]) => {
          const normalized =
            typeof chunk === "string"
              ? normalizeScrollbackSafeInkWrite(chunk)
              : Buffer.isBuffer(chunk)
                ? Buffer.from(normalizeScrollbackSafeInkWrite(chunk.toString("utf8")), "utf8")
                : chunk
          return value.call(target, normalized, ...args)
        }
      }
      return typeof value === "function" ? value.bind(target) : value
    },
    set(target, property, value, receiver) {
      return Reflect.set(target, property, value, receiver)
    },
  }) as NodeJS.WriteStream
}

export interface StdoutRowDiagnostics {
  readonly actualRows: number | null
  readonly fallbackRows: number | null
  readonly resolvedRows: number
  readonly source: "actual" | "fallback" | "default"
}

export interface StdoutColumnDiagnostics {
  readonly actualColumns: number | null
  readonly fallbackColumns: number | null
  readonly resolvedColumns: number
  readonly source: "actual" | "fallback" | "default"
}

const normalizeRows = (value: number | null | undefined): number | null =>
  Number.isFinite(value) && Number(value) > 0 ? Number(value) : null

const normalizeColumns = (value: number | null | undefined): number | null =>
  Number.isFinite(value) && Number(value) > 0 ? Number(value) : null

export const resolveStdoutRowDiagnostics = (
  fallback?: number | null,
  actual: number | null | undefined = process.stdout?.rows,
): StdoutRowDiagnostics => {
  const actualRows = normalizeRows(actual)
  const fallbackRows = normalizeRows(fallback)
  if (actualRows != null) {
    return { actualRows, fallbackRows, resolvedRows: actualRows, source: "actual" }
  }
  if (fallbackRows != null) {
    return { actualRows, fallbackRows, resolvedRows: fallbackRows, source: "fallback" }
  }
  return { actualRows, fallbackRows, resolvedRows: 40, source: "default" }
}

export const resolveActualStdoutRows = (fallback?: number | null): number =>
  resolveStdoutRowDiagnostics(fallback).resolvedRows

export const resolveStdoutColumnDiagnostics = (
  fallback?: number | null,
  actual: number | null | undefined = process.stdout?.columns,
): StdoutColumnDiagnostics => {
  const actualColumns = normalizeColumns(actual)
  const fallbackColumns = normalizeColumns(fallback)
  if (actualColumns != null) {
    return { actualColumns, fallbackColumns, resolvedColumns: actualColumns, source: "actual" }
  }
  if (fallbackColumns != null) {
    return { actualColumns, fallbackColumns, resolvedColumns: fallbackColumns, source: "fallback" }
  }
  return { actualColumns, fallbackColumns, resolvedColumns: 80, source: "default" }
}

export const resolveActualStdoutColumns = (fallback?: number | null): number =>
  resolveStdoutColumnDiagnostics(fallback).resolvedColumns
