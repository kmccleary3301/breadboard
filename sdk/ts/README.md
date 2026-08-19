# `@breadboard/sdk`

Product-only TypeScript SDK for the BreadBoard 26-operation HTTP/SSE surface.

The client mirrors `contracts/public/operations.v2.json` and the generated
`docs/contracts/cli_bridge/openapi.json`. Internal and legacy endpoints are not
part of this package's ordinary client.

## Usage

```ts
import { createBreadboardClient } from "@breadboard/sdk"

const client = createBreadboardClient({ baseUrl: "http://127.0.0.1:9099" })
const harness = await client.createHarness()
const lock = await client.lockHarness(harness.data.path as string)
const started = await client.startSession({
  lock_id: lock.data.path,
  task: "Hi",
})
const session = started.data.session as { session_id: string }

for await (const event of client.eventsSession(session.session_id)) {
  console.log(event.type, event.payload)
}
```

