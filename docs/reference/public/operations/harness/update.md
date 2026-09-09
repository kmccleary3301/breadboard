<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:8288cc7cd5f032ceec82963798984daed187e18c83478228085b057fbae52b85 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness.update -->
<!-- slug: operations/harness/update -->

# update harness

Candidate public operation reference for `harness.update`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `PUT /v1/harnesses/{harness_id}` |
| CLI | `breadboard harness update` |
| Lifecycle | `sync` |
| Effects | `write` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness.write` |

## Bindings

- OpenAPI: `PUT /v1/harnesses/{harness_id}` (`harness.update`)
- Python: `BreadBoardClient.update_harness`
- TypeScript: `BreadBoardClient.updateHarness`
- TUI: `public.harness.update` (`action`)
- CLI: `breadboard harness update`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness.update.input.v1`
- Output catalog ID (unpublished): `bb.harness.update.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
