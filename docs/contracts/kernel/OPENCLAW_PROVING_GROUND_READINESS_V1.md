# OpenClaw Proving-Ground Readiness V1

This document captures when OpenClaw becomes an honest proving ground for the TS kernel effort.

## Preconditions now satisfied

- kernel doctrines are tracked
- semantic dossiers are in place for the scoped kernel/runtime families
- the schema pack exists and is consumed by TS packages
- the engine conformance manifest and gate exist
- Python reference fixtures exist for major primitive families
- TS contract package and kernel-core substrate now exist
- execution capability / placement semantics are explicit
- trusted-local, OCI, and delegated-remote driver families are real
- OpenClaw host-bridge acceptance fixtures are frozen for the scoped supported slices

## Current bounded proving-ground status

OpenClaw is now a real proving ground for the scoped TypeScript host/runtime slices:

1. supported embedded text lane
2. transcript continuation lane
3. narrow tool-bearing lane
4. provider-quirk preservation lane
5. direct trusted-local execution
6. OCI-backed execution
7. delegated remote execution

The remaining limitation is breadth, not honesty:

- OpenClaw is still not a full Pi replacement target
- ACP and richer channel/delivery surfaces remain out of scope
- broader tool-rich parity remains future work

## Rule

OpenClaw is a proving ground after the kernel substrate is strong enough to pressure-test. It is not the source of truth that defines the kernel contracts.
