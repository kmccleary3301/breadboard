import { readFile } from "node:fs/promises"
import { ApiError, createBreadboardClient } from "@breadboard/sdk"

const request = JSON.parse(await readFile(0, "utf8"))
const client = createBreadboardClient({
  baseUrl: request.base_url,
  authToken: request.auth_token,
})

try {
  const result = await client.invokePublicAction(request.action_id, request.input ?? {})
  if (request.action_id === "public.session.events") {
    const events = []
    for await (const event of result) events.push(event)
    process.stdout.write(JSON.stringify({ ok: true, result: events }))
  } else {
    process.stdout.write(JSON.stringify({ ok: true, result }))
  }
} catch (error) {
  if (error instanceof ApiError) {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: { status: error.status, body: error.body },
    }))
    process.exitCode = 2
  } else {
    throw error
  }
}
