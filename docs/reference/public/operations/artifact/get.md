<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: artifact.get -->
<!-- slug: operations/artifact/get -->

# get artifact

Candidate public operation reference for `artifact.get`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/artifacts/{artifact_id}` |
| CLI | `bbh artifact get` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.artifact.read` |

## Bindings

- OpenAPI: `GET /v1/artifacts/{artifact_id}` (`artifact.get`)
- Python: `BreadBoardClient.get_artifact`
- TypeScript: `BreadBoardClient.getArtifact`
- TUI: `public.artifact.get` (`view`)
- CLI: `bbh artifact get`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.artifact.get.input.v1`
- Output catalog ID (unpublished): `bb.artifact.get.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
