<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:e85d665fd3617f1130bc7e604682eedf30a7076e40d904a9088914d0e26ee45b -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness.lock -->
<!-- slug: operations/harness/lock -->

# lock harness

Candidate public operation reference for `harness.lock`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/harnesses/{harness_id}/lock` |
| CLI | `breadboard harness lock` |
| Lifecycle | `sync` |
| Effects | `write` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness.write` |

## Bindings

- OpenAPI: `POST /v1/harnesses/{harness_id}/lock` (`harness.lock`)
- Python: `BreadBoardClient.lock_harness`
- TypeScript: `BreadBoardClient.lockHarness`
- TUI: `public.harness.lock` (`action`)
- CLI: `breadboard harness lock`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness.lock.input.v1`
- Output catalog ID (unpublished): `bb.harness.lock.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
