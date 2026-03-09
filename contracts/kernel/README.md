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

- run request / context
- kernel event
- transcript/session snapshot
- provider exchange
- tool spec / call / execution / render
- permission / approval record
- middleware lifecycle
- task / subagent / background task
- checkpoint / longrun state
- execution capability / placement
- sandbox request / result
- distributed task descriptor
- transcript continuation patch
- unsupported case taxonomy
- replay session
- conformance evidence
- import/export session

---

## Change policy

Any future artifact added here should correspond to:

1. a semantic dossier under `docs/contracts/kernel/semantics/`
2. fixture coverage under `conformance/engine_fixtures/`
3. explicit version metadata
