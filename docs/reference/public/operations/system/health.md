<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 5 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:627448eb95b9e12c1f951c1b5d5fd30a4fc870cb57c520f23bb3d8c4338394ec -->
<!-- document-kind: operation-reference -->
<!-- operation-id: system.health -->
<!-- slug: operations/system/health -->

# health system

Candidate public operation reference for `system.health`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/health` |
| CLI | `bbh system health` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `none` (`references_only`) |
| Capabilities | none |

## Bindings

- OpenAPI: `GET /v1/health` (`system.health`)
- Python: `BreadBoardClient.health_system`
- TypeScript: `BreadBoardClient.healthSystem`
- TUI: `public.system.health` (`view`)
- CLI: `bbh system health`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.system.health.input.v1`
- Output catalog ID (unpublished): `bb.system.health.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
