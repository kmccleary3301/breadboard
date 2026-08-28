<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.start -->
<!-- slug: operations/session/start -->

# start session

Candidate public operation reference for `session.start`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/sessions` |
| CLI | `bbh harness run` |
| Lifecycle | `async` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `keyed` — The caller supplies an idempotency key scoped to the operation and canonical input. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/sessions` (`session.start`)
- Python: `BreadBoardClient.start_session`
- TypeScript: `BreadBoardClient.startSession`
- TUI: `public.session.start` (`action`)
- CLI: `bbh harness run`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.start.input.v1`
- Output catalog ID (unpublished): `bb.session.start.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: [`bb.kernel_event.v2`](../../../../../contracts/kernel/schemas/bb.kernel_event.v2.schema.json)
