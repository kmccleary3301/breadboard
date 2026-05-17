import { Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { forgetSession } from "../cache/sessionCache.js"
import type { SessionSummary } from "../api/types.js"
import { listCachedSessions, rememberSession, loadSessionCache } from "../cache/sessionCache.js"
import { getCliApi, reportApiCommandErrorEffect } from "./commandRuntime.js"
import { normalizeTableJsonOutputMode } from "./commandOutput.js"
import { renderSimpleTable } from "./commandTable.js"
import { reportValidationErrorEffect } from "./commandValidation.js"
import { printCommandPresentation } from "./commandPresentation.js"
import { renderStoppedSessionLine } from "./commandLifecycle.js"

const outputFlag = Options.text("output").pipe(Options.withDefault("table"))
const stopFlag = Options.text("stop").pipe(Options.optional)
const stopAllFlag = Options.boolean("stop-all").pipe(Options.optional)


interface SessionRow {
  readonly sessionId: string
  readonly status: string
  readonly createdAt: string
  readonly lastActivityAt: string
  readonly model?: string
  readonly source: "backend" | "cache"
}

export const mergeSessions = async (backend: SessionSummary[]): Promise<SessionRow[]> => {
  if (backend.length > 0) {
    await Promise.all(backend.map((summary) => rememberSession(summary)))
  }
  const cache = await loadSessionCache()
  const cacheEntries = backend.length > 0 ? await listCachedSessions() : Object.values(cache.sessions)
  const rows: SessionRow[] = []
  const seen = new Set<string>()
  for (const summary of backend) {
    rows.push({
      sessionId: summary.session_id,
      status: summary.status,
      createdAt: summary.created_at,
      lastActivityAt: summary.last_activity_at,
      model: (summary.metadata?.model as string | undefined) ?? undefined,
      source: "backend",
    })
    seen.add(summary.session_id)
  }
  for (const cached of cacheEntries) {
    if (seen.has(cached.sessionId)) continue
    rows.push({
      sessionId: cached.sessionId,
      status: cached.status,
      createdAt: cached.createdAt,
      lastActivityAt: cached.lastActivityAt,
      model: cached.model,
      source: "cache",
    })
  }
  return rows.sort((a, b) => (a.lastActivityAt > b.lastActivityAt ? -1 : 1))
}

const renderTable = (rows: SessionRow[]): string => {
  if (rows.length === 0) {
    return "No sessions found."
  }
  return renderSimpleTable(
    ["Session", "Status", "Last Activity", "Model", "Source"],
    rows.map((row) => [row.sessionId, row.status, formatTimestamp(row.lastActivityAt), row.model ?? "-", row.source]),
    {
      separatorChar: "─",
      trimTrailingWhitespace: true,
    },
  )
}

const formatTimestamp = (value: string): string => {
  try {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) {
      return value
    }
    return date.toISOString().replace(".000Z", "Z")
  } catch {
    return value
  }
}

export const sessionsCommand = Command.make("sessions", { output: outputFlag, stop: stopFlag, stopAll: stopAllFlag }, ({ output, stop, stopAll }) =>
  Effect.gen(function* () {
    const api = getCliApi()
    const stopTarget = Option.getOrNull(stop)
    const stopAllValue = Option.getOrNull(stopAll) ?? false
    if (stopAllValue && stopTarget) {
      return yield* reportValidationErrorEffect("Specify either --stop or --stop-all, not both.")
    }
    if (stopAllValue) {
      const listResult = yield* Effect.either(
        Effect.tryPromise({
          try: () => api.listSessions(),
          catch: (error) => error as Error,
        }),
      )
      if (listResult._tag === "Left") {
        yield* reportApiCommandErrorEffect("list sessions", listResult.left)
        return
      }
      const running = listResult.right.filter((session) => session.status === "running")
      if (running.length === 0) {
        yield* Console.log("No running sessions to stop.")
        return
      }
      for (const session of running) {
        const stopResult = yield* Effect.either(
          Effect.tryPromise({
            try: () => api.deleteSession(session.session_id),
            catch: (error) => error as Error,
          }),
        )
        if (stopResult._tag === "Left") {
          yield* reportApiCommandErrorEffect(`stop ${session.session_id}`, stopResult.left)
        } else {
          yield* Console.log(renderStoppedSessionLine(session.session_id))
          yield* Effect.tryPromise({
            try: () => forgetSession(session.session_id),
            catch: (error) => error as Error,
          }).pipe(Effect.catchAll(() => Effect.succeed(undefined)))
        }
      }
      return
    }
    if (stopTarget) {
      const stopResult = yield* Effect.either(
        Effect.tryPromise({
          try: () => api.deleteSession(stopTarget),
          catch: (error) => error as Error,
        }),
      )
      if (stopResult._tag === "Left") {
        yield* reportApiCommandErrorEffect("stop session", stopResult.left)
      } else {
        yield* Console.log(renderStoppedSessionLine(stopTarget))
        yield* Effect.tryPromise({
          try: () => forgetSession(stopTarget),
          catch: (error) => error as Error,
        }).pipe(Effect.catchAll(() => Effect.succeed(undefined)))
      }
      return
    }
    try {
      const list = yield* Effect.promise(() => api.listSessions())
      const merged = yield* Effect.promise(() => mergeSessions(list))
      const mode = normalizeTableJsonOutputMode(output)
      yield* Effect.promise(() => printCommandPresentation({ mode, jsonValue: merged, text: renderTable(merged) }))
    } catch (error) {
      yield* reportApiCommandErrorEffect("list sessions", error)
      return yield* Effect.fail(error as Error)
    }
  }),
)
