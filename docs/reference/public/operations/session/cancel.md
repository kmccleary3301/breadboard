<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 4 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:23c3596e075e609539ede6ba64a005110ceb85935f046e0246ff36cd39b990f6 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.cancel -->
<!-- slug: operations/session/cancel -->

# cancel session

Candidate public operation reference for `session.cancel`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/sessions/{session_id}/cancel` |
| CLI | `bbh session cancel` |
| Lifecycle | `async` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `keyed` — The caller supplies an idempotency key scoped to the operation and canonical input. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/sessions/{session_id}/cancel` (`session.cancel`)
- Python: `BreadBoardClient.cancel_session`
- TypeScript: `BreadBoardClient.cancelSession`
- TUI: `public.session.cancel` (`action`)
- CLI: `bbh session cancel`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.cancel.input.v1`
- Output catalog ID (unpublished): `bb.session.cancel.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: [`bb.public_session_event.v1`](../../../../../contracts/public/schemas/bb.public_session_event.v1.schema.json)
