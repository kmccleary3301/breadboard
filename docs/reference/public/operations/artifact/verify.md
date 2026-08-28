<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: artifact.verify -->
<!-- slug: operations/artifact/verify -->

# verify artifact

Candidate public operation reference for `artifact.verify`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/artifacts/{artifact_id}/verify` |
| CLI | `bbh artifact verify` |
| Lifecycle | `sync` |
| Effects | `verify` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.artifact.verify` |

## Bindings

- OpenAPI: `POST /v1/artifacts/{artifact_id}/verify` (`artifact.verify`)
- Python: `BreadBoardClient.verify_artifact`
- TypeScript: `BreadBoardClient.verifyArtifact`
- TUI: `public.artifact.verify` (`action`)
- CLI: `bbh artifact verify`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.artifact.verify.input.v1`
- Output catalog ID (unpublished): `bb.artifact.verify.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
