<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:d574352b4e55997181883cb316d1e9db13c8715636e610c992bb1c156a8e622b -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.adopt -->
<!-- slug: operations/session/adopt -->

# adopt an exact Lock into a session

Candidate public operation reference for `session.adopt`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/sessions/{session_id}/adoptions` |
| CLI | `breadboard session adopt` |
| Lifecycle | `sync` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `idempotent` — The request_id identifies an exact-Lock adoption request; repeating the same request_id and exact canonical input has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/sessions/{session_id}/adoptions` (`session.adopt`)
- Python: `BreadBoardClient.adopt_session`
- TypeScript: `BreadBoardClient.adoptSession`
- TUI: `public.session.adopt` (`action`)
- CLI: `breadboard session adopt`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.adopt.input.v1`
- Output catalog ID (unpublished): `bb.session.adopt.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
