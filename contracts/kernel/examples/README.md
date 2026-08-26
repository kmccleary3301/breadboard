# Kernel Contract Examples

This directory will hold minimal valid examples for each kernel contract family.

Examples should be:

- human-readable
- intentionally small
- suitable for validation in both Python and TypeScript
- paired with semantic dossiers and fixture bundles

These are examples of contract shape, not complete replay or host integration artifacts.
The provider exchange V2 examples below are the kernel aggregate
(`bb.provider_exchange.v2`); they are distinct from the untouched public
attempt-level `contracts/public/schemas/bb.provider_exchange.v2.schema.json`.

Current minimal examples exist for:

- kernel event
- session transcript
- tool call
- tool spec
- tool execution outcome
- tool model render
- run request
- run context
- provider exchange V1 (`provider_exchange_minimal.json`)
- provider exchange V2 done (`provider_exchange_v2_done.json`)
- provider exchange V2 error (`provider_exchange_v2_error.json`)
- provider exchange V2 cancelled (`provider_exchange_v2_cancelled.json`)
- permission
- execution capability
- execution placement
- sandbox request / result
- distributed task descriptor
- transcript continuation patch
- unsupported case
- replay session
- task
- checkpoint metadata
