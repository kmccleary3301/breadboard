<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 5 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:23c3596e075e609539ede6ba64a005110ceb85935f046e0246ff36cd39b990f6 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness.create -->
<!-- slug: operations/harness/create -->

# create harness

Candidate public operation reference for `harness.create`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/harnesses` |
| CLI | `bbh harness create` |
| Lifecycle | `sync` |
| Effects | `write` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness.write` |

## Bindings

- OpenAPI: `POST /v1/harnesses` (`harness.create`)
- Python: `BreadBoardClient.create_harness`
- TypeScript: `BreadBoardClient.createHarness`
- TUI: `public.harness.create` (`action`)
- CLI: `bbh harness create`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness.create.input.v1`
- Output catalog ID (unpublished): `bb.harness.create.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
