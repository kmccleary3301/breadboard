<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:d574352b4e55997181883cb316d1e9db13c8715636e610c992bb1c156a8e622b -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness.publish -->
<!-- slug: operations/harness/publish -->

# publish harness generation

Candidate public operation reference for `harness.publish`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/harness-publications/{target}` |
| CLI | `breadboard harness publish` |
| Lifecycle | `sync` |
| Effects | `write` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same request identity and input has the same outcome; mismatched reuse conflicts. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness.write` |

## Bindings

- OpenAPI: `POST /v1/harness-publications/{target}` (`harness.publish`)
- Python: `BreadBoardClient.publish_harness`
- TypeScript: `BreadBoardClient.publishHarness`
- TUI: `public.harness.publish` (`action`)
- CLI: `breadboard harness publish`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness.publish.input.v1`
- Output catalog ID (unpublished): `bb.harness.publish.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
