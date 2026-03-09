# @breadboard/kernel-contracts

Typed contract pack for the shared BreadBoard kernel program.

Scope:
- exports machine-readable kernel schemas
- exports TypeScript contract types
- exports small validator helpers for contract examples and fixtures
- exports the engine conformance manifest contract

Non-goals:
- no engine loop logic
- no provider runtime implementation
- no hidden in-process BreadBoard engine

This package exists to keep the shared kernel truth surface separate from both:
- the existing CLI-bridge client SDK (`sdk/ts`)
- the future TS kernel/core engine packages
