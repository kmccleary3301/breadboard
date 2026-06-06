import { describe, expect, it } from "vitest"
import { extractUsageMetrics } from "../src/commands/repl/controllerUtils.js"

describe("extractUsageMetrics", () => {
  it("extracts usage metrics from payload.usage", () => {
    expect(
      extractUsageMetrics({
        usage: {
          prompt_tokens: 12,
          completion_tokens: 34,
          total_tokens: 46,
          cost_usd: 0.00012,
          latency_ms: 512,
        },
      }),
    ).toEqual({
      promptTokens: 12,
      completionTokens: 34,
      totalTokens: 46,
      costUsd: 0.00012,
      latencyMs: 512,
    })
  })

  it("extracts usage metrics from completion payload summaries", () => {
    expect(
      extractUsageMetrics({
        summary: {
          completed: true,
          prompt_tokens: 10,
          completion_tokens: 20,
          total_tokens: 30,
        },
      }),
    ).toEqual({
      promptTokens: 10,
      completionTokens: 20,
      totalTokens: 30,
      costUsd: undefined,
      latencyMs: undefined,
    })
  })

  it("keeps supporting top-level usage-shaped payloads", () => {
    expect(
      extractUsageMetrics({
        promptTokens: 3,
        completionTokens: 4,
        totalTokens: 7,
        costUsd: 0.01,
        latencyMs: 250,
      }),
    ).toEqual({
      promptTokens: 3,
      completionTokens: 4,
      totalTokens: 7,
      costUsd: 0.01,
      latencyMs: 250,
    })
  })

  it("returns null when no usage fields are present", () => {
    expect(extractUsageMetrics({ summary: { completed: true } })).toBeNull()
  })
})
