import { describe, expect, it } from "vitest"
import { Effect } from "effect"
import { reportValidationErrorEffect, validationError } from "../src/commands/commandValidation.js"

describe("commandValidation", () => {
  it("creates validation errors with the original message", () => {
    expect(validationError("bad input").message).toBe("bad input")
  })

  it("fails effect-based validation with the original message", async () => {
    const exit = await Effect.runPromiseExit(reportValidationErrorEffect("bad input"))
    expect(exit._tag).toBe("Failure")
  })
})
