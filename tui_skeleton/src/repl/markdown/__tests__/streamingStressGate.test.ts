import { describe, expect, it } from "vitest"
import { evaluateJitterGate } from "../jitterGate.js"

describe("streaming stress gate", () => {
  it("keeps fenced-code streaming churn within strict threshold", () => {
    const baselineFrames = [
      "```ts\nconst a = 1;\n",
      "```ts\nconst A = 1;\nconst b = 2;\n",
    ]
    const candidateFrames = [
      "```ts\nconst a = 1;\n",
      "```ts\nconst a = 1;\nconst b = 2;\n",
    ]
    const result = evaluateJitterGate(baselineFrames, candidateFrames, {
      maxPrefixChurnDelta: 0,
      maxReflowDelta: 0,
    })
    expect(result.ok).toBe(true)
  })

  it("keeps table streaming churn within strict threshold", () => {
    const baselineFrames = [
      "| c1 | c2 |\n| --- | --- |\n| 1 | 2 |\n",
      "| c1 | c2 |\n| --- | --- |\n| 1 | 22 |\n| 3 | 4 |\n",
    ]
    const candidateFrames = [
      "| c1 | c2 |\n| --- | --- |\n| 1 | 2 |\n",
      "| c1 | c2 |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n",
    ]
    const result = evaluateJitterGate(baselineFrames, candidateFrames, {
      maxPrefixChurnDelta: 0,
      maxReflowDelta: 0,
    })
    expect(result.ok).toBe(true)
  })
})
