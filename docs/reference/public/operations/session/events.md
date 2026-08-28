<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.events -->
<!-- slug: operations/session/events -->

# events session

Candidate public operation reference for `session.events`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/sessions/{session_id}/events` |
| CLI | `bbh session events` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.read` |

## Bindings

- OpenAPI: `GET /v1/sessions/{session_id}/events` (`session.events`)
- Python: `BreadBoardClient.events_session`
- TypeScript: `BreadBoardClient.eventsSession`
- TUI: `public.session.events` (`view`)
- CLI: `bbh session events`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.events.input.v1`
- Output catalog ID (unpublished): `bb.session.events.result.v1`
- Response transport: SSE `text/event-stream`
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
