# Engine Conformance Fixtures

This directory is reserved for cross-engine fixture bundles used to compare BreadBoard interpreters against the shared kernel contract.

Initial intended use:

- Python reference-engine vs frozen contract expectations
- Python reference-engine vs TypeScript embedded-engine
- comparator-class-specific fixture bundles

---

## What belongs here

Examples:

- canonical examples for contract families
- differential fixture bundles
- comparator manifests
- expected normalized traces
- model-visible rendering baselines
- support-tier and exemption manifests when appropriate

---

## Comparator classes to support

At minimum:

- shape-equal
- normalized-trace-equal
- model-visible-equal
- workspace-side-effects-equal
- projection-equal

Not every contract or lane needs the same comparator class, but each fixture bundle should declare which class it is meant to satisfy.

## Support tiers

The first manifest rows should use one of:

- `draft-shape`
- `draft-semantic`
- `reference-engine`

These tiers indicate how strong a fixture family is as a future gate for
cross-engine conformance.

Canonical validation entrypoints:

- `python scripts/validate_engine_conformance_manifest.py`
- `python scripts/compare_kernel_conformance_engines.py`
- `python scripts/validate_kernel_contract_fixtures.py`
- `python scripts/run_kernel_conformance_gate.py`

---

## Relationship to existing conformance work

This directory does not replace the current E4 and conformance materials under `docs/conformance/`.

Instead, it extends that discipline to the multi-engine problem:

- E4 target freeze manifests prove target-harness alignment
- engine fixture bundles should prove cross-engine alignment to the shared kernel contract

---

## First fixture families to add

1. kernel event examples
2. transcript derivation examples
3. provider exchange normalization fixtures
4. tool lifecycle and render fixtures
5. replay session round-trip fixtures
6. permission lifecycle fixtures
7. task/subagent lifecycle fixtures
8. execution capability / placement fixtures
9. sandbox roundtrip fixtures
10. transcript continuation patch fixtures
11. unsupported-case fixtures
12. distributed task / orchestration fixtures
