import { Command, Args, Options } from "@effect/cli"
import { Console, Effect } from "effect"
import { runResume } from "./resumeLogic.js"
import type { SessionEvent, SessionSummary } from "../api/types.js"
import { CliProviders } from "../providers/cliProviders.js"

const sessionArg = Args.text({ name: "session-id" })
const outputOption = Options.text("output").pipe(Options.withDefault("text"))

interface RenderSections {
  conversation: string[]
  tools: string[]
}

export const buildRenderSections = (events: SessionEvent[]): RenderSections => {
  const conversation: string[] = []
  const tools: string[] = []
  for (const event of events) {
    const turnLabel = event.turn != null ? ` (turn ${event.turn})` : ""
    switch (event.type) {
      case "assistant_message": {
        const text = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
        conversation.push(`ASSISTANT${turnLabel}: ${text}`)
        break
      }
      case "user_message": {
        const text = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
        conversation.push(`USER${turnLabel}: ${text}`)
        break
      }
      case "tool_call": {
        tools.push(`TOOL CALL${turnLabel}: ${JSON.stringify(event.payload)}`)
        break
      }
      case "tool_result": {
        tools.push(`TOOL RESULT${turnLabel}: ${JSON.stringify(event.payload)}`)
        break
      }
      case "reward_update": {
        tools.push(`REWARD${turnLabel}: ${JSON.stringify(event.payload.summary ?? event.payload)}`)
        break
      }
      case "error": {
        tools.push(`ERROR${turnLabel}: ${JSON.stringify(event.payload)}`)
        break
      }
      case "completion": {
        tools.push(`COMPLETION${turnLabel}: ${JSON.stringify(event.payload.summary ?? event.payload)}`)
        break
      }
      default:
        break
    }
  }
  return { conversation, tools }
}

export const renderCommand = Command.make("render", { session: sessionArg, output: outputOption }, ({ session, output }) =>
  Effect.tryPromise(async () => {
    try {
      const result = await runResume({ sessionId: session })
      let summary: SessionSummary | null = null
      try {
        summary = await CliProviders.sdk.api().getSession(session)
      } catch (error) {
        await Console.error(`Warning: failed to load session summary (${(error as Error).message})`)
      }
      const sections = buildRenderSections(result.events)
      if (output === "json") {
        await Console.log(
          JSON.stringify(
            {
              sessionId: session,
              completion: result.completion,
              events: result.events,
              summary,
              sections,
            },
            null,
            2,
          ),
        )
        return
      }
      await Console.log(`Session: ${session}`)
      await Console.log(`Status: ${summary?.status ?? "unknown"}`)
      await Console.log("\n=== Conversation ===")
      if (sections.conversation.length === 0) {
        await Console.log("(no conversation events)")
      } else {
        await Console.log(sections.conversation.join("\n"))
      }
      if (sections.tools.length > 0) {
        await Console.log("\n=== Tools & Events ===")
        await Console.log(sections.tools.join("\n"))
      }
      if (summary?.reward_summary) {
        await Console.log(`\nRewards: ${JSON.stringify(summary.reward_summary)}`)
      }
      if (result.completion) {
        await Console.log(`\nCompletion: ${JSON.stringify(result.completion)}`)
      } else if (summary?.completion_summary) {
        await Console.log(`\nCompletion: ${JSON.stringify(summary.completion_summary)}`)
      }
      if (summary?.logging_dir) {
        await Console.log(`\nLogging dir: ${summary.logging_dir}`)
      }
      await Console.log(`\nEvents received: ${result.events.length}`)
    } catch (error) {
      await Console.error((error as Error).message)
      throw error
    }
  }),
)
