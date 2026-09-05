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
  console.log(event.kind, event.payload)
}
```

Session streams use the strict `bb.public_session_event.v1` projection. The
canonical kernel `bb.kernel_event.v2` envelope remains a separate internal
contract.
Each `payload_schema_version` resolves to the public lifecycle or annotation
payload schema, or a registered kernel observation payload schema.

`annotation` carries an immutable annotation ID, message/trajectory target,
label, author, and generation. It is host-visible, never model/provider input.
`follow: false` snapshots retain annotations written after Session settlement;
live followers still stop at settlement.

Public Session records expose the pinned `generation_id`, current
`trajectory_segment_id`, and nullable child `lineage`. Child start and settlement
events preserve their parent/root Session and parent/child Work Item identities.
Assistant observations preserve paired `message_id`/`trajectory_id` values.
`WorldFieldMask` permits only `["/occurred_at", "/timestamp"]`.

Product engine-management, canonical session, and lifecycle interfaces are
available from `@breadboard/sdk/engine`, `@breadboard/sdk/session`, and
`@breadboard/sdk/lifecycle`. These are explicit supported entrypoints; the
package has no unstable catch-all entrypoint.

