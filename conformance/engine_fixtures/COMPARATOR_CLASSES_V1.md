# Comparator Classes V1

This document defines the first shared comparator vocabulary for the engine-conformance program.

## Comparator classes

1. `shape-equal`
   - structural equality only
   - useful for early schema-pack and example validation

2. `normalized-trace-equal`
   - normalized event sequence equality
   - ignores transport/private details but preserves semantic order
   - appropriate for replay-session and event-lineage fixtures

3. `model-visible-equal`
   - compares the exact content that the model would effectively observe
   - appropriate for transcript and tool-render fixtures

4. `workspace-side-effects-equal`
   - compares resulting workspace state / file changes / side effects
   - appropriate once delegated or full-engine workspace fixtures exist
   - appropriate for backend-mediated execution lanes once filesystem and artifact digests are frozen strongly enough

5. `projection-equal`
   - compares host projection output derived from kernel events
   - appropriate for UI/client reducers, never as kernel truth

## Backend-mediated execution note

Execution-driver and sandbox lanes frequently need two different comparators:

- `normalized-trace-equal` for request/result and lineage semantics
- `workspace-side-effects-equal` for stronger backend-mediated side-effect parity

The V2 program currently freezes the first class broadly and only begins preparing the second class where backend evidence is strong enough.

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

## Gating rule

- `draft-shape` rows gate schema and package plumbing only
- `draft-semantic` rows gate early implementation work but not public parity claims
- `reference-engine` rows are the minimum tier for cross-engine equivalence work that aspires to be a real BreadBoard runtime claim
