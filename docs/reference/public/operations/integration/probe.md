<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:8288cc7cd5f032ceec82963798984daed187e18c83478228085b057fbae52b85 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: integration.probe -->
<!-- slug: operations/integration/probe -->

# probe integration

Candidate public operation reference for `integration.probe`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/integrations/{integration_id}/probe` |
| CLI | `breadboard integration probe` |
| Lifecycle | `async` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `keyed` — The caller supplies an idempotency key scoped to the operation and canonical input. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.integration.execute` |

## Bindings

- OpenAPI: `POST /v1/integrations/{integration_id}/probe` (`integration.probe`)
- Python: `BreadBoardClient.probe_integration`
- TypeScript: `BreadBoardClient.probeIntegration`
- TUI: `public.integration.probe` (`action`)
- CLI: `breadboard integration probe`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.integration.probe.input.v1`
- Output catalog ID (unpublished): `bb.integration.probe.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
