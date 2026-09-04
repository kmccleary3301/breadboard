# @breadboard/execution-driver-remote

Delegated/remote execution-driver package for BreadBoard's V2 TypeScript runtime.

Current scope:
- remote/delegated placement compatibility
- remote sandbox-request construction
- adapter-driven remote execution boundary
- fetch-backed HTTP execution adapter for delegated sandbox requests
- scheduled execution lifecycle (`submit`, `observe`, `cancel`) with bounded polling
- Ray host-bridge driver registration
- direct SSH Slurm submission with durable request receipts, `squeue`/`sacct`
  observation, ambiguous-ack recovery, and `scancel` cleanup
- timeout/error-envelope handling for delegated execution
- explicit non-claim of direct sandbox implementation

Provider IDs, scheduler state, and backend artifact locations are emitted only
through the external evidence callback. Kernel and Product Session payloads
receive content-addressed, provider-neutral `SandboxResultV1` data.

This package exists so delegated execution remains behind the shared execution-driver
contract rather than leaking backend-specific request shapes into the kernel.
