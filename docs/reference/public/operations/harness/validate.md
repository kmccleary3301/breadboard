<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness.validate -->
<!-- slug: operations/harness/validate -->

# validate harness

Candidate public operation reference for `harness.validate`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/harnesses/{harness_id}/validate` |
| CLI | `bbh harness validate` |
| Lifecycle | `sync` |
| Effects | `verify` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness.verify` |

## Bindings

- OpenAPI: `POST /v1/harnesses/{harness_id}/validate` (`harness.validate`)
- Python: `BreadBoardClient.validate_harness`
- TypeScript: `BreadBoardClient.validateHarness`
- TUI: `public.harness.validate` (`action`)
- CLI: `bbh harness validate`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness.validate.input.v1`
- Output catalog ID (unpublished): `bb.harness.validate.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
