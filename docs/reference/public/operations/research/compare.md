<!-- GENERATED FILE - do not edit by hand. -->
<!-- generator: scripts/quality/generate_public_bindings.py -->
<!-- generator-version: 6 -->
<!-- catalog-id: bb.public_operation_catalog.v2 -->
<!-- catalog-sha256: sha256:8288cc7cd5f032ceec82963798984daed187e18c83478228085b057fbae52b85 -->
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
| CLI | `breadboard research compare` |
| Lifecycle | `sync` |
| Effects | `execute` |
| Stability | `experimental` |
| Idempotency | `idempotent` — Canonical input bytes resume or return the existing durable run and report; never duplicate them. |
| Authentication | `capability_gated` (`references_only`) |
| Capabilities | `public.session.execute`, `public.session.read` |

## Bindings

- OpenAPI: `POST /v1/research/compare` (`research.compare`)
- Python: `BreadBoardClient.compare_research`
- TypeScript: `BreadBoardClient.compareResearch`
- TUI: `public.research.compare` (`action`)
- CLI: `breadboard research compare`
- Documentation: `product-docs owner` (`candidate`)

## Schemas

- Input catalog ID (unpublished): `bb.research.compare.input.v1`
- Output catalog ID (unpublished): `bb.research.compare.result.v1`
- Response transport: JSON `PublicResult` (`bb.cli.result.v1`)
- Error: [`bb.problem.v1`](../../../../../contracts/public/schemas/bb.problem.v1.schema.json)
- Event: none
