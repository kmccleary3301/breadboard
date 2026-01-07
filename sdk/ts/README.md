# `@breadboard/sdk`

Minimal TypeScript SDK for the BreadBoard CLI bridge API (HTTP + SSE).

This package is intentionally small and mirrors the contract exported under:
- `docs/contracts/cli_bridge/openapi.json`
- `docs/contracts/cli_bridge/schemas/*`

## Usage

```ts
import { createBreadboardClient, streamSessionEvents } from "@breadboard/sdk"

const client = createBreadboardClient({ baseUrl: "http://127.0.0.1:9099" })

const session = await client.createSession({ config_path: "agent_configs/opencode_mock_c_fs.yaml", task: "Hi" })

for await (const event of streamSessionEvents(session.session_id, { config: { baseUrl: "http://127.0.0.1:9099" } })) {
  console.log(event.type, event.payload)
}
```

