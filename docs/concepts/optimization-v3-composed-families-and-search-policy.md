# Optimization V3 Composed Families And Search Policy

Optimization V3 starts where V2 stops.

V2 made atomic optimization families real:

- evaluation suites
- objective suites
- target families
- bounded search spaces
- staged optimization on fixed methodology
- family-level promotion evidence

What V2 did **not** solve is the next real post-V2 pressure:

> how to optimize bounded compositions of existing families without turning reward or backend heuristics into a second truth system.

## Public center

V3 adds one new public non-kernel center:

- `FamilyCompositionManifest`

This artifact exists to compose existing families narrowly.

It is **not**:

- a campaign plan
- a generic optimizer graph
- a reward authoring surface
- a co-optimization DSL

Its initial role is to declare:

- member family ids
- shared target scope
- composition kind
- suite bindings
- search-space binding
- review and promotion class
- applicability scope
- cross-family invariants
- runtime-context requirements

## Search-space evolution

V3 extends `SearchSpaceManifest` only minimally for composition:

- `coupled_loci_groups`
- `stage_partitions`
- `cross_family_constraints`

These fields exist to make bounded composition explicit and validator-checked.

They do **not** introduce:

- arbitrary graph search
- schema mutation
- archive or genealogy semantics

## Reward boundary

BreadBoard now has two value-adjacent lanes:

- optimize’s evaluation/objective/comparison/promotion stack
- the separate `reward` aggregation lane used for episode-level telemetry and scoring summaries

V3 keeps the boundary explicit:

- evaluation remains primary truth
- objective suites remain derived optimization-layer structure
- reward-style scalarization, when needed, remains derived search policy
- the `reward` aggregation lane does **not** become the public truth model for optimization

That means:

- no public `RewardSuiteManifest` in V3
- no reward-led promotion
- no reward-first optimizer architecture

## Nano / Mini experiment policy

V3 uses explicit cost discipline.

### `GPT-5.4 Nano` default

Nano should be the default for:

- proposal sweeps
- low-risk composed-family runs
- reflective summarization
- cheap adjudication when executable checks dominate
- baseline comparisons

### `GPT-5.4 Mini` escalation only

Mini should be used only when clearly justified for:

- high-complexity composed-family proposal generation
- ambiguous semantic adjudication that Nano cannot resolve reliably
- verifier-guided repair in coding/E4 lanes when Nano stalls
- top-slice candidates that already cleared Nano-first sweeps

### Fairness rule

Do not compare:

- backend A on Nano
- backend B on Mini

unless the point of the experiment is explicitly to study model-tier effects rather than optimizer quality.

## First live composed lane

The first V3 proving lane should stay low-blast-radius:

- tool guidance family
- coding-overlay family

This is the safest first composition because:

- it stays on one shared dossier package
- it is interpretable
- it is Nano-valid
- it exercises real cross-family coupling without touching the riskiest families first

## Second live composed lane

The second V3 lane should prove a bounded E4 dossier prompt+config composition:

- support / execution family
- coding-overlay family

This lane matters because it forces V3 to handle:

- one config-like family
- one prompt-like family
- support-sensitive review burden
- bounded coding-harness discipline
- one shared dossier package with explicit runtime-context assumptions

The point is not broader search. The point is to prove that V3 can compose a real E4 dossier lane without losing:

- replay-safe execution-profile honesty
- apply_patch-centered bounded editing
- hidden-hold discipline
- cost discipline

### Model-tier policy for the bounded E4 lane

This lane should run:

- `GPT-5.4 Nano` by default
- `GPT-5.4 Mini` only by explicit escalation policy

The escalation policy should be:

- auditable
- justified by ambiguity or close hidden-hold margin
- disallowed as a silent backend advantage

So a clean first implementation should record:

- Nano as the default model tier
- whether Mini escalation was considered
- whether it triggered
- why it did or did not trigger

## Composition-aware promotion and generalization

Once composed lanes are real, promotion evidence can no longer stay atomic-family-shaped.

V3 therefore needs composition-aware promotion evidence that carries:

- `composition_id`
- member-family coverage
- coupling-risk summary
- composition-specific hidden-hold and regression coverage
- transfer-slice identifiers where the lane actually spans package or model-tier boundaries

This is still promotion discipline, not new ontology.

It should remain:

- non-kernel
- review-sensitive
- derived from benchmark, comparison, and objective-breakdown evidence

It should not become:

- a generic transfer-learning framework
- a reward-led promotion system
- an excuse to auto-promote support-sensitive composed lanes

## What stays private

The following remain backend-private or experimental:

- selection/scalarization policy
- staged optimizer internals
- Nano→Mini escalation rules
- verifier escalation policy

V3 deliberately avoids implying that those are canonical public centers.

## What V3 still does not do

V3 does **not** introduce:

- DARWIN campaign state
- archive / island / genealogy ontology
- online self-tuning
- public reward-suite ontology
- generic co-optimization workflow products

The point of V3 is to prove bounded composition cleanly, not to reopen the whole optimizer architecture.
