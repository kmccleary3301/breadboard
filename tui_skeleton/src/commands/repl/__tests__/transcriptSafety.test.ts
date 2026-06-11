import { afterEach, describe, expect, it } from "vitest"
import {
  assertDurableTranscriptSafe,
  findDurableTranscriptSafetyIssues,
} from "../transcriptSafety.js"

const previousStrict = process.env.BREADBOARD_TUI_STRICT_TRANSCRIPT_SAFETY

afterEach(() => {
  if (previousStrict === undefined) {
    delete process.env.BREADBOARD_TUI_STRICT_TRANSCRIPT_SAFETY
  } else {
    process.env.BREADBOARD_TUI_STRICT_TRANSCRIPT_SAFETY = previousStrict
  }
})

describe("durable transcript safety", () => {
  const rawProviderPayload =
    "[error] Error code: 401 - {'error': {'message': \"Missing scopes: api.responses.write.\", 'type': 'invalid_request_error'}}"

  it("flags raw provider payloads on diagnostic durable surfaces", () => {
    expect(
      findDurableTranscriptSafetyIssues(rawProviderPayload, { surface: "conversation", speaker: "system" }),
    ).toHaveLength(3)
    expect(
      findDurableTranscriptSafetyIssues(rawProviderPayload, { surface: "tool", kind: "error" }),
    ).toHaveLength(3)
  })

  it("does not flag user or assistant rows that may legitimately contain JSON examples", () => {
    expect(
      findDurableTranscriptSafetyIssues(rawProviderPayload, { surface: "conversation", speaker: "user" }),
    ).toHaveLength(0)
    expect(
      findDurableTranscriptSafetyIssues(rawProviderPayload, { surface: "conversation", speaker: "assistant" }),
    ).toHaveLength(0)
  })

  it("throws only when strict safety mode is enabled", () => {
    delete process.env.BREADBOARD_TUI_STRICT_TRANSCRIPT_SAFETY
    expect(() =>
      assertDurableTranscriptSafe(rawProviderPayload, { surface: "tool", kind: "error" }, "tool:error"),
    ).not.toThrow()

    process.env.BREADBOARD_TUI_STRICT_TRANSCRIPT_SAFETY = "1"
    expect(() =>
      assertDurableTranscriptSafe(rawProviderPayload, { surface: "tool", kind: "error" }, "tool:error"),
    ).toThrow(/Unsafe durable transcript row/)
  })
})
