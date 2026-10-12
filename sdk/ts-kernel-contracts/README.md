# @breadboard/kernel-contracts

Typed contract pack for the shared BreadBoard kernel program.

Scope:
- exports machine-readable kernel schemas
- exports TypeScript contract types
- exports small validator helpers for contract examples and fixtures
- exports the engine conformance manifest contract
- exports execution capability / placement / sandbox / unsupported-case contract types

Non-goals:
- no engine loop logic
- no provider runtime implementation
- no hidden in-process BreadBoard engine

This package exists to keep the shared kernel truth surface separate from both:
- the existing CLI-bridge client SDK (`sdk/ts`)
- the future TS kernel/core engine packages

## Provider contract generation

`npm run generate` reads the canonical kernel schemas. Provider exchange v2
uses a compile-only projection in `scripts/provider-exchange-types.ts` to
distribute tool-call argument variants and replay dispatch conditions into
TypeScript unions. Runtime registries retain the original schema, including
its negative constraints, bounds, and conditional payload validation. No
schema acceptance or frozen digest changes are needed for this projection.

Generic replay envelopes carry a private type-only validation brand. Native
replay literals retain their exact provider/version/payload contracts, while
arbitrary generic tags cannot bypass those contracts. Parse untrusted exchanges
with `kernelValidators.providerExchangeV2(value)`, which narrows successful
values to `ProviderExchangeV2` using the existing canonical generated validator.
The brand adds no runtime field or wrapper and is never serialized.

The Python provider contracts keep request identity and transport normalization
in `contract_requests.py`, tool definitions/calls/results in `contract_tools.py`,
and message content, reasoning, replay, and results in `contract_messages.py`.
`contracts.py` remains the public facade.

Hook-denied Responses tool searches retain their correlated error output;
successful native search outputs retain their original wire fields.

