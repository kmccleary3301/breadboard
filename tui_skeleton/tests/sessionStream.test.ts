import { describe, expect, it } from "vitest"
import { collectSessionStream, completionSummary } from "../src/commands/sessionStream.js"
import type { SessionEvent } from "../src/api/types.js"

async function* fromEvents(events: SessionEvent[]) {
  for (const event of events) {
    yield event
  }
}

describe("sessionStream", () => {
  it("collects events until completion and returns summary payload", async () => {
    const seen: string[] = []
    const events = [
      { type: "assistant.message.delta", payload: { text: "hi" } },
      { type: "completion", payload: { summary: { ok: true }, ignored: true } },
      { type: "assistant_message", payload: { text: "late" } },
    ] as SessionEvent[]

    const result = await collectSessionStream(fromEvents(events), async (event) => {
      seen.push(event.type)
    })

    expect(seen).toEqual(["assistant.message.delta", "completion"])
    expect(result.events).toHaveLength(2)
    expect(result.completion).toEqual({ ok: true })
  })

  it("falls back to full completion payload when summary is absent", () => {
    const event = { type: "completion", payload: { ok: true } } as SessionEvent
    expect(completionSummary(event)).toEqual({ ok: true })
  })
})
