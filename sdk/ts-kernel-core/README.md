# @breadboard/kernel-core

Future TypeScript kernel/runtime package for BreadBoard.

Current scope:
- contract-supporting helpers only
- tracked fixture / manifest consumers
- transcript / task / checkpoint normalization helpers
- execution capability / placement helpers
- transcript continuation patch and unsupported-case helpers
- constrained static-text execution slice for contract-first kernel events and transcript output
- constrained scripted-tool execution slice for contract-first tool-call, tool-result, and transcript semantics
- constrained provider-aware text execution slice for contract-first provider-exchange consumption
- provider-aware continuation slice with transcript continuation patch output
- conformance summary helpers over the tracked fixture and manifest substrate
- no provider runtime implementation
- no hidden in-process engine
- no claim of parity with the Python reference engine

This package exists so future TS engine logic has one obvious home instead of leaking into:
- `sdk/ts` (CLI-bridge client)
- UI packages
- host integrations
