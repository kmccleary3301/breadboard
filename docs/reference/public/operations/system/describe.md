<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:60c05131d160ca2a746df72715a339798793c8ae51c5749cadcb4f97f60bdc9c -->
<!-- document-kind: operation-reference -->
<!-- operation-id: system.describe -->
<!-- slug: operations/system/describe -->

# describe system

Candidate public operation reference for `system.describe`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/system` |
| CLI | `bbh system describe` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `none` (`references_only`) |
| Capabilities | none |

## Bindings

- OpenAPI: `GET /v1/system` (`system.describe`)
- Python: `BreadBoardClient.describe_system`
- TypeScript: `BreadBoardClient.describeSystem`
- TUI: `public.system.describe` (`view`)
- CLI: `bbh system describe`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.system.describe.input.v1`
- Output catalog ID (unpublished): `bb.system.describe.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
