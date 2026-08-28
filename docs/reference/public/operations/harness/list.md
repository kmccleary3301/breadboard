<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:60c05131d160ca2a746df72715a339798793c8ae51c5749cadcb4f97f60bdc9c -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness.list -->
<!-- slug: operations/harness/list -->

# list harness

Candidate public operation reference for `harness.list`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/harnesses` |
| CLI | `bbh harness list` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness.read` |

## Bindings

- OpenAPI: `GET /v1/harnesses` (`harness.list`)
- Python: `BreadBoardClient.list_harness`
- TypeScript: `BreadBoardClient.listHarness`
- TUI: `public.harness.list` (`view`)
- CLI: `bbh harness list`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness.list.input.v1`
- Output catalog ID (unpublished): `bb.harness.list.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
