# Comparator Classes V1

This document defines the first shared comparator vocabulary for the engine-conformance program.

## Comparator classes

1. `shape-equal`
   - structural equality only
   - useful for early schema-pack and example validation

2. `normalized-trace-equal`
   - normalized event sequence equality
   - ignores transport/private details but preserves semantic order

3. `model-visible-equal`
   - compares the exact content that the model would effectively observe

4. `workspace-side-effects-equal`
   - compares resulting workspace state / file changes / side effects

5. `projection-equal`
   - compares host projection output derived from kernel events

## Rule

Every engine-fixture family should declare which comparator class applies. There is no universal equality mode for every lane.
