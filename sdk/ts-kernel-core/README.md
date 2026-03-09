# @breadboard/kernel-core

Future TypeScript kernel/runtime package for BreadBoard.

Current scope:
- contract-supporting helpers only
- tracked fixture / manifest consumers
- transcript / task / checkpoint normalization helpers
- no provider runtime implementation
- no hidden in-process engine
- no claim of parity with the Python reference engine

This package exists so future TS engine logic has one obvious home instead of leaking into:
- `sdk/ts` (CLI-bridge client)
- UI packages
- host integrations
