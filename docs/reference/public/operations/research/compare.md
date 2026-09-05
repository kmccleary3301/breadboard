<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 5 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:5b7eeb4e9c1f7eb01f4fc9906cc46f00b6d34d630da052f28a339178f679c51e -->
<!-- document-kind: operation-reference -->
<!-- operation-id: research.compare -->
<!-- slug: operations/research/compare -->

# compare research

Candidate public operation reference for `research.compare`.

## Contract

| Field | Value |
| --- | --- |
| Status | `candidate` |
| HTTP | `POST /v1/research/compare` |
| CLI | `bbh research compare` |
| Lifecycle | `sync` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Canonical input bytes resume or return the existing durable run and report; never duplicate them. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute` |

## Bindings

- OpenAPI: `POST /v1/research/compare` (`research.compare`)
- Python: `BreadBoardClient.compare_research`
- TypeScript: `BreadBoardClient.compareResearch`
- TUI: `public.research.compare` (`action`)
- CLI: `bbh research compare`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.research.compare.input.v1`
- Output catalog ID (unpublished): `bb.research.compare.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
