<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: harness_lock.get -->
<!-- slug: operations/harness-lock/get -->

# get harness lock

Candidate public operation reference for `harness_lock.get`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/harness-locks/{lock_id}` |
| CLI | `bbh harness-lock get` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.harness_lock.read` |

## Bindings

- OpenAPI: `GET /v1/harness-locks/{lock_id}` (`harness_lock.get`)
- Python: `BreadBoardClient.get_harness_lock`
- TypeScript: `BreadBoardClient.getHarnessLock`
- TUI: `public.harness_lock.get` (`view`)
- CLI: `bbh harness-lock get`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.harness_lock.get.input.v1`
- Output catalog ID (unpublished): `bb.harness_lock.get.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
