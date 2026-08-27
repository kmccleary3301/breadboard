<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 2 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.send_input -->
<!-- slug: operations/session/send-input -->

# send input session

Candidate public operation reference for `session.send_input`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/sessions/{session_id}/input` |
| CLI | `bbh session send-input` |
| Lifecycle | `async` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `keyed` — The caller supplies an idempotency key scoped to the operation and canonical input. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/sessions/{session_id}/input` (`session.send_input`)
- Python: `BreadBoardClient.send_input_session`
- TypeScript: `BreadBoardClient.sendInputSession`
- TUI: `public.session.send_input` (`action`)
- CLI: `bbh session send-input`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.send_input.input.v1`
- Output catalog ID (unpublished): `bb.session.send_input.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: [`bb.kernel_event.v2`](../../../../../contracts/kernel/schemas/bb.kernel_event.v2.schema.json)
