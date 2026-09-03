<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 4 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:23c3596e075e609539ede6ba64a005110ceb85935f046e0246ff36cd39b990f6 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: integration.get -->
<!-- slug: operations/integration/get -->

# get integration

Candidate public operation reference for `integration.get`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/integrations/{integration_id}` |
| CLI | `bbh integration get` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.integration.read` |

## Bindings

- OpenAPI: `GET /v1/integrations/{integration_id}` (`integration.get`)
- Python: `BreadBoardClient.get_integration`
- TypeScript: `BreadBoardClient.getIntegration`
- TUI: `public.integration.get` (`view`)
- CLI: `bbh integration get`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.integration.get.input.v1`
- Output catalog ID (unpublished): `bb.integration.get.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
