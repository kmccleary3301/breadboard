<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:d574352b4e55997181883cb316d1e9db13c8715636e610c992bb1c156a8e622b -->
<!-- document-kind: operation-reference -->
<!-- operation-id: system.schemas -->
<!-- slug: operations/system/schemas -->

# schemas system

Candidate public operation reference for `system.schemas`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/schemas` |
| CLI | `breadboard system schemas` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `none` (`references_only`) |
| Capabilities | none |

## Bindings

- OpenAPI: `GET /v1/schemas` (`system.schemas`)
- Python: `BreadBoardClient.schemas_system`
- TypeScript: `BreadBoardClient.schemasSystem`
- TUI: `public.system.schemas` (`view`)
- CLI: `breadboard system schemas`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.system.schemas.input.v1`
- Output catalog ID (unpublished): `bb.system.schemas.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
