# Python Service Boundary Matrix V1

This matrix classifies current Python subsystems for the multi-engine effort.

## Portable kernel-owned surfaces

These should converge into shared kernel contracts and alternate-engine implementations.

- event envelope semantics
- transcript derivation
- provider exchange normalization
- tool lifecycle and model render
- permission and guardrail semantics
- middleware lifecycle
- task/subagent lineage
- replay session semantics

## Delegated heavy-service surfaces

These are expected to remain Python-backed initially.

- sandbox and snapshot execution
- Ray-backed orchestration
- Python-specific workspace execution services that rely on existing infra

## Ambiguous / future-decision surfaces

These need later resolution once the TS kernel is stronger.

- longrun controller orchestration details beyond portable checkpoint metadata
- hybrid execution of complex workspace mutation flows
- any service that couples kernel semantics to infrastructure state too tightly

## Rule

When in doubt:

1. preserve the semantic kernel contract in shared form
2. delegate the infrastructure-heavy implementation
3. do not let the delegated service own the semantic truth surface
