import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { ApiError } from "../api/client.js"
import { getMostRecentSessionId } from "../cache/sessionCache.js"
import { runResume } from "./resumeLogic.js"

const outputOption = Options.text("output").pipe(Options.withDefault("text"))

const normalizeMode = (value: string): "text" | "json" => (value === "json" ? "json" : "text")

export const resumeCommand = Command.make(
  "resume",
  {
    session: Args.text({ name: "session-id" }).pipe(Args.optional),
    output: outputOption,
  },
  ({ session, output }) =>
    Effect.tryPromise(async () => {
      const requestedId = Option.getOrNull(session)
      const sessionId = requestedId ?? (await getMostRecentSessionId())
      if (!sessionId) {
        await Console.error("No session id provided and no recent session found.")
        return
      }
      try {
        const result = await runResume(
          { sessionId },
          async (event) => {
            if (event.type !== "completion") {
              await Console.log(renderEvent(event))
            }
          },
        )
        const mode = normalizeMode(output)
        if (mode === "json") {
          await Console.log(JSON.stringify({ sessionId, events: result.events, completion: result.completion }, null, 2))
        }
      } catch (error) {
        if (error instanceof ApiError) {
          await Console.error(`Failed to resume session (status ${error.status})`)
          if (error.body) {
            await Console.error(JSON.stringify(error.body))
          }
        } else {
          await Console.error((error as Error).message)
        }
        throw error
      }
    }),
)

export const renderEvent = (event: Record<string, any>): string => {
  switch (event.type) {
    case "assistant_message": {
      const payload = event.payload ?? {}
      return typeof payload.text === "string" ? payload.text : JSON.stringify(payload)
    }
    case "tool_call":
    case "tool_result":
    case "reward_update":
    case "error":
      return `[${event.type}] ${JSON.stringify(event.payload)}`
    default:
      return ""
  }
}
