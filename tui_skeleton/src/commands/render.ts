import { Command, Args, Options } from "@effect/cli"
import { Effect } from "effect"
import { runResume } from "./resumeLogic.js"
import type { SessionEvent, SessionSummary } from "../api/types.js"
import { getCliApi, reportCommandWarning } from "./commandRuntime.js"
import { normalizeTextJsonOutputMode } from "./commandOutput.js"
import { renderLabeledLine } from "./commandText.js"
import { printDetailCommandResult } from "./commandApiPresenter.js"
import { runCommandWithReportedError } from "./commandOutcome.js"

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
    await runCommandWithReportedError({
      run: async () => {
        const result = await runResume({ sessionId: session })
        let summary: SessionSummary | null = null
        try {
          summary = await getCliApi().getSession(session)
        } catch (error) {
          await reportCommandWarning("failed to load session summary", error)
        }
        const sections = buildRenderSections(result.events)
        const mode = normalizeTextJsonOutputMode(output)
        await printDetailCommandResult({
          mode,
          title: "Session render",
          jsonValue: {
            sessionId: session,
            completion: result.completion,
            events: result.events,
            summary,
            sections,
          },
          lines: [renderLabeledLine("Session", session), renderLabeledLine("Status", summary?.status ?? "unknown")],
          sections: [
            { title: "Conversation", lines: sections.conversation, emptyText: "(no conversation events)" },
            ...(sections.tools.length > 0 ? [{ title: "Tools & Events", lines: sections.tools }] : []),
          ],
          jsonBlocks: [
            { label: "Rewards", value: summary?.reward_summary },
            { label: "Completion", value: result.completion ?? summary?.completion_summary },
          ],
          trailingLines: [
            ...(summary?.logging_dir ? [renderLabeledLine("Logging dir", summary.logging_dir)] : []),
            renderLabeledLine("Events received", String(result.events.length)),
          ],
        })
      },
      report: async (error) => {
        process.stderr.write(`${(error as Error).message}\n`)
      },
    })
  }),
)
