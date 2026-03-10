# @breadboard/execution-drivers

Shared execution-driver contracts and helper logic for TypeScript-side BreadBoard runtime work.

This package owns:
- execution-driver interface shape
- placement compatibility helpers
- driver selection and planned-execution helpers
- side-effect expectation helpers
- evidence expectation helpers
- unsupported-case helpers for execution-driver negotiation
- persistent terminal-session driver contracts

This package does not own:
- kernel truth
- host bridge logic
- OCI runtime implementation details
- microVM lifecycle logic

The intended layering is:

1. `ts-kernel-core` decides what execution capability is required.
2. An execution placement is selected for that capability.
3. This package chooses a compatible driver and constructs a planned execution record.
4. Driver-specific packages build backend-shaped sandbox requests.
5. Backend execution returns a `bb.sandbox_result.v1` that the kernel can interpret without
   learning backend-private lifecycle details.

For persistent terminal/process sessions, driver packages may additionally expose:

- terminal session startup
- stdin / poll / signal interactions
- derived registry snapshots
- cleanup/control results

Those flows remain distinct from one-shot sandbox execution.
