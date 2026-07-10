# BreadBoard Kernel Contract Pack

This directory is the machine-readable home for the shared BreadBoard kernel contract pack.

It should eventually contain:

- versioned JSON Schemas
- example payloads
- generated binding metadata if needed
- contract manifest metadata

This directory exists now as an explicit scaffold so that the multi-engine program has a tracked home in the repo from the start.

---

## Intended structure

- `schemas/` — versioned schema artifacts such as `bb.kernel_event.v1.schema.json`
- `examples/` — minimal valid examples for each contract family
- `registries/` — machine-readable identifier registries that are the single source of truth for shared enumerations
- `manifests/` — optional package/version manifests if we formalize them

---

## Scope

This directory is for the shared kernel contract only.

It is not for:

- UI reducer contracts
- host-only projection schemas
- engine-private Python or TypeScript configuration details

Those may reference the kernel, but they should not redefine it.

---

## Initial contract families

The machine-readable source of truth for initial contract families is
`contracts/kernel/registries/kernel_families.v1.json`, validated by
`contracts/kernel/schemas/bb.registry.v1.schema.json`.

Do not maintain a divergent literal family list in this README. Human-readable
kernel doctrine and semantic docs may summarize the families, but additions,
deprecations, and no-schema-yet reservations belong in the registry.

`contracts/kernel/registries/e4_adapters.v1.json` is the registry for E4 lane
definition adapter ids. It uses `bb.registry.v1`; `scripts/e4_parity/lane_definitions.py`
adds the E4-specific check that `normalize.translator`, `compare.comparator`, and
`capture.adapter` resolve to active rows with the expected `metadata.kind`.

---

## Change policy

Any future artifact added here should correspond to:

1. a semantic dossier under `docs/contracts/kernel/semantics/`
2. fixture coverage under `conformance/engine_fixtures/`
3. explicit version metadata
