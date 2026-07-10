import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { getMostRecentSessionId } from "../cache/sessionCache.js"
import { reportApiCommandError } from "./commandRuntime.js"
import { normalizeTextJsonOutputMode } from "./commandOutput.js"
import { reportValidationError } from "./commandValidation.js"
import { printCommandPresentation } from "./commandPresentation.js"
import { runCommandWithReportedError } from "./commandOutcome.js"
import { runResume } from "./resumeLogic.js"

const outputOption = Options.text("output").pipe(Options.withDefault("text"))


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
        await reportValidationError("No session id provided and no recent session found.")
        return
      }
      const result = await runCommandWithReportedError({
        run: () =>
          runResume(
            { sessionId },
            async (event) => {
              if (event.type !== "completion") {
                await Console.log(renderEvent(event))
              }
            },
          ),
        report: (error) => reportApiCommandError("resume session", error),
      })
      const mode = normalizeTextJsonOutputMode(output)
      await printCommandPresentation({
        mode,
        jsonValue: { sessionId, events: result.events, completion: result.completion },
      })
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
