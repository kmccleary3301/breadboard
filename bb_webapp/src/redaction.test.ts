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

  it("redacts nested auth/session fields across mixed payload shapes", () => {
    const payload = {
      headers: {
        Authorization: "Bearer top-secret",
        "set-cookie": "session=abc",
      },
      replay: {
        session_token: "token-123",
        deep: [{ apiKey: "sk-123" }, { normal: "ok" }],
      },
      metadata: {
        session_id: "visible-id",
        nested: {
          cookieStore: "x",
          user: "safe",
        },
      },
    }
    const redacted = redactSensitiveValue(payload) as Record<string, unknown>
    const headers = redacted.headers as Record<string, unknown>
    const replay = redacted.replay as Record<string, unknown>
    const metadata = redacted.metadata as Record<string, unknown>
    const deep = replay.deep as Array<Record<string, unknown>>

    expect(headers.Authorization).toBe("[REDACTED]")
    expect(headers["set-cookie"]).toBe("[REDACTED]")
    expect(replay.session_token).toBe("[REDACTED]")
    expect(deep[0].apiKey).toBe("[REDACTED]")
    expect(deep[1].normal).toBe("ok")
    expect(metadata.session_id).toBe("[REDACTED]")
    expect((metadata.nested as Record<string, unknown>).cookieStore).toBe("[REDACTED]")
    expect((metadata.nested as Record<string, unknown>).user).toBe("safe")
  })
})
