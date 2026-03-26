# DARWIN Architecture Boundary V0

Date: 2026-03-14

## Three-plane freeze

DARWIN Phase-1 uses three planes:

1. **Control plane**
   - campaign compilation
   - policy registry
   - budget classes
   - promotion / rollback rules
2. **Data plane**
   - lane execution
   - topology execution
   - tool use
   - artifact emission
3. **Evaluation plane**
   - scorecards
   - evidence bundles
   - claim records
   - drift / contamination / replay gates

## Kernel vs extension rule

BreadBoard kernel remains generalization-first.

DARWIN logic stays outside the kernel unless all of the following are true:

- cross-lane evidence exists from ATP + at least two non-ATP lanes,
- semantics are stable across two release cycles,
- rollback is explicit and cheap.

## What stays extension-scoped in Phase-1

- DARWIN contract pack
- DARWIN registries
- DARWIN scorecards
- DARWIN lane baseline runners
- DARWIN evidence / claim emitters
- all lane-specific evaluation wrappers
- C-Trees feature-flag logic

## What must not happen

- ATP-specific heuristics promoted as DARWIN-wide kernel behavior
- hidden claim logic inside lane prompts or lane-only scripts
- runtime renames of `evolake` compatibility surfaces without a migration tranche
