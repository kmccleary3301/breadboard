<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 4 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:23c3596e075e609539ede6ba64a005110ceb85935f046e0246ff36cd39b990f6 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: session.list -->
<!-- slug: operations/session/list -->

# list session

Candidate public operation reference for `session.list`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/sessions` |
| CLI | `bbh session list` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.read` |

## Bindings

- OpenAPI: `GET /v1/sessions` (`session.list`)
- Python: `BreadBoardClient.list_session`
- TypeScript: `BreadBoardClient.listSession`
- TUI: `public.session.list` (`view`)
- CLI: `bbh session list`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.session.list.input.v1`
- Output catalog ID (unpublished): `bb.session.list.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
