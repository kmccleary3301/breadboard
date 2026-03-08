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

## Support tiers

Use these support tiers in the engine conformance manifest:

1. `draft-shape`
   - schema and basic shape are frozen
   - semantics are still incomplete

2. `draft-semantic`
   - intended semantic meaning is documented
   - evidence is useful for cross-engine implementation work
   - still not strong enough for public parity claims

3. `reference-engine`
   - Python reference interpreter has produced the fixture/evidence bundle
   - the bundle is strong enough to gate future alternative-engine work
