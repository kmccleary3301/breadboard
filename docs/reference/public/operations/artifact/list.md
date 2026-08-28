<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 3 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:4deb87f3b82f7ccdf922fb609a9784840fb01ee591793e95f2af42acd2097674 -->
<!-- document-kind: operation-reference -->
<!-- operation-id: artifact.list -->
<!-- slug: operations/artifact/list -->

# list artifact

Candidate public operation reference for `artifact.list`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `GET /v1/artifacts` |
| CLI | `bbh artifact list` |
| Lifecycle | `sync` |
| Effects | `read` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Repeating the same canonical request has the same outcome. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.artifact.read` |

## Bindings

- OpenAPI: `GET /v1/artifacts` (`artifact.list`)
- Python: `BreadBoardClient.list_artifact`
- TypeScript: `BreadBoardClient.listArtifact`
- TUI: `public.artifact.list` (`view`)
- CLI: `bbh artifact list`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.artifact.list.input.v1`
- Output catalog ID (unpublished): `bb.artifact.list.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
