import { describe, expect, it } from "vitest"
import { ApiError } from "../src/api/client.js"
import {
  formatApiCommandError,
  formatApiFailure,
  formatCommandError,
  formatCommandWarning,
  formatRequestFailure,
} from "../src/commands/commandRuntime.js"

describe("formatApiCommandError", () => {
  it("formats ApiError with status and body", () => {
    const error = new ApiError("boom", 418, { detail: "teapot" })
    expect(formatApiCommandError("brew tea", error)).toEqual([
      "Failed to brew tea (status 418)",
      JSON.stringify({ detail: "teapot" }),
    ])
  })

  it("formats ApiError without body", () => {
    const error = new ApiError("boom", 404)
    expect(formatApiCommandError("read file", error)).toEqual(["Failed to read file (status 404)"])
  })

  it("falls back to the standard error message", () => {
    expect(formatApiCommandError("list sessions", new Error("nope"))).toEqual(["nope"])
  })
})

describe("formatApiFailure", () => {
  it("formats ApiError with a labeled failure line", () => {
    const error = new ApiError("boom", 503, { retry: true })
    expect(formatApiFailure("Health check failed", error)).toEqual([
      "Health check failed (503).",
      JSON.stringify({ retry: true }),
    ])
  })

  it("formats non-ApiError values with a message suffix", () => {
    expect(formatApiFailure("Model catalog failed", new Error("offline"))).toEqual([
      "Model catalog failed: offline",
    ])
  })
})

describe("formatRequestFailure", () => {
  it("formats ApiError request failures with status and body", () => {
    const error = new ApiError("Request failed", 418, { detail: "teapot" })
    expect(formatRequestFailure(error)).toEqual([
      "Request failed (status 418)",
      JSON.stringify({ detail: "teapot" }),
    ])
  })

  it("formats generic request failures as plain messages", () => {
    expect(formatRequestFailure(new Error("boom"))).toEqual(["boom"])
  })
})

describe("formatCommandError", () => {
  it("formats generic command errors with a label", () => {
    expect(formatCommandError("Resume failed", new Error("offline"))).toEqual(["Resume failed: offline"])
  })
})


describe("formatCommandWarning", () => {
  it("formats non-fatal warnings using the warning prefix", () => {
    const error = new ApiError("boom", 503, { retry: true })
    expect(formatCommandWarning("failed to load session summary", error)).toEqual([
      "Warning: failed to load session summary (503).",
      JSON.stringify({ retry: true }),
    ])
  })
})
