<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 2 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.resume -->
<!-- slug: operations/session/resume -->

# resume session

Candidate public operation reference for `session.resume`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/sessions/{session_id}/resume` |
| CLI | `bbh session resume` |
| Lifecycle | `async` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `keyed` — The caller supplies an idempotency key scoped to the operation and canonical input. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/sessions/{session_id}/resume` (`session.resume`)
- Python: `BreadBoardClient.resume_session`
- TypeScript: `BreadBoardClient.resumeSession`
- TUI: `public.session.resume` (`action`)
- CLI: `bbh session resume`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.resume.input.v1`
- Output catalog ID (unpublished): `bb.session.resume.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: [`bb.kernel_event.v2`](../../../../../contracts/kernel/schemas/bb.kernel_event.v2.schema.json)
