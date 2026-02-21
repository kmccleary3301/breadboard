import { describe, expect, it } from "vitest"
import { redactSensitiveValue } from "./redaction"

describe("redaction", () => {
  it("redacts sensitive keys recursively", () => {
    const redacted = redactSensitiveValue({
      token: "abc",
      nested: { api_key: "123", ok: true },
      arr: [{ password: "x" }, { value: "safe" }],
    }) as Record<string, unknown>
    expect(redacted.token).toBe("[REDACTED]")
    expect((redacted.nested as Record<string, unknown>).api_key).toBe("[REDACTED]")
    expect(((redacted.arr as unknown[])[0] as Record<string, unknown>).password).toBe("[REDACTED]")
    expect(((redacted.arr as unknown[])[1] as Record<string, unknown>).value).toBe("safe")
  })
})
