import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiClient, createApiClient, type AttachmentUploadPayload } from "../client.js"

const ORIGINAL_ENV = {
  BREADBOARD_API_TOKEN: process.env.BREADBOARD_API_TOKEN,
  BREADBOARD_API_URL: process.env.BREADBOARD_API_URL,
}

afterEach(() => {
  for (const [key, value] of Object.entries(ORIGINAL_ENV)) {
    if (value === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = value
    }
  }
  vi.restoreAllMocks()
})

const attachment: AttachmentUploadPayload = {
  filename: "note.txt",
  mime: "text/plain",
  base64: Buffer.from("hello").toString("base64"),
  size: 5,
}

describe("createApiClient upload auth", () => {
  it("resolves async auth tokens before uploading attachments", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ attachments: [{ id: "att-1" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )
    const authToken = vi.fn(async () => "upload-token")
    const client = createApiClient({ baseUrl: "http://127.0.0.1:9099", authToken })

    await expect(client.uploadAttachments("session-1", [attachment])).resolves.toEqual([{ id: "att-1" }])

    expect(authToken).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ Authorization: "Bearer upload-token" })
  })

  it("resolves env auth tokens through ApiClient before uploading attachments", async () => {
    process.env.BREADBOARD_API_URL = "http://127.0.0.1:9099"
    process.env.BREADBOARD_API_TOKEN = "upload-env-token"
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ attachments: [{ id: "att-1" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )

    await expect(ApiClient.uploadAttachments("session-1", [attachment])).resolves.toEqual([{ id: "att-1" }])

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ Authorization: "Bearer upload-env-token" })
  })

  it("aborts upload before fetch when async auth exceeds request timeout", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }))
    const authToken = vi.fn(() => new Promise<string>(() => undefined))
    const client = createApiClient({ baseUrl: "http://127.0.0.1:9099", authToken, requestTimeoutMs: 1 })

    await expect(client.uploadAttachments("session-1", [attachment])).rejects.toThrow("Aborted")

    expect(authToken).toHaveBeenCalledOnce()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("does not fetch when async auth provider throws synchronously", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }))
    const authToken = vi.fn(() => {
      throw new Error("sync auth failure")
    })
    const client = createApiClient({ baseUrl: "http://127.0.0.1:9099", authToken })

    await expect(client.uploadAttachments("session-1", [attachment])).rejects.toThrow("sync auth failure")

    expect(authToken).toHaveBeenCalledOnce()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
