<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:d574352b4e55997181883cb316d1e9db13c8715636e610c992bb1c156a8e622b -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.approve -->
<!-- slug: operations/session/approve -->

# approve session

Candidate public operation reference for `session.approve`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/sessions/{session_id}/approve` |
| CLI | `breadboard session approve` |
| Lifecycle | `async` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `keyed` — The caller supplies an idempotency key scoped to the operation and canonical input. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/sessions/{session_id}/approve` (`session.approve`)
- Python: `BreadBoardClient.approve_session`
- TypeScript: `BreadBoardClient.approveSession`
- TUI: `public.session.approve` (`action`)
- CLI: `breadboard session approve`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.approve.input.v1`
- Output catalog ID (unpublished): `bb.session.approve.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: [`bb.public_session_event.v1`](../../../../../contracts/public/schemas/bb.public_session_event.v1.schema.json)
