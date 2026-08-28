<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: integration.list -->
<!-- slug: operations/integration/list -->

# list integration

Candidate public operation reference for `integration.list`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/integrations` |
| CLI | `bbh integration list` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.integration.read` |

## Bindings

- OpenAPI: `GET /v1/integrations` (`integration.list`)
- Python: `BreadBoardClient.list_integration`
- TypeScript: `BreadBoardClient.listIntegration`
- TUI: `public.integration.list` (`view`)
- CLI: `bbh integration list`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.integration.list.input.v1`
- Output catalog ID (unpublished): `bb.integration.list.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
