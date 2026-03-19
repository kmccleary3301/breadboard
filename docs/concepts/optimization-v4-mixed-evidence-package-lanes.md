# Optimization V4 Mixed-Evidence Package Lanes

Optimization V4 starts where V3 stops.

V3 made bounded family composition real:

- `FamilyCompositionManifest`
- composition-aware search spaces
- two live composed-family lanes
- composed-family promotion and generalization evidence
- backend-private search policy
- Nano-first / auditable Mini-escalation discipline

What V3 did **not** solve is the next post-V3 pressure:

> how to optimize a real bounded package across 3 families under mixed evidence and honest transfer/applicability claims without turning private scalarization into a second truth system.

## Public center

V4 keeps the public center narrow.

It does **not** add another composition artifact.

It adds one new public non-kernel artifact:

- `TransferSliceManifest`

This artifact exists because package-level and model-tier applicability claims are now structurally important and cannot stay raw `transfer_slice_ids` metadata forever.

Its role is to declare:

- slice identity
- slice kind
- selector or binding metadata
- promotion role
- visibility
- small metadata only

It is **not**:

- a transfer-learning framework
- a package graph
- a search policy
- a reward surface

## Mixed evidence

V4 extends existing suite artifacts rather than replacing them.

### `EvaluationSuiteManifest`

V4 adds narrow `signal_channels` so suites can declare evidence classes such as:

- executable checks
- verifier outputs
- diagnostics
- semantic-judge signals
- review gates

This remains descriptive. It is not a mini evidence DSL.

### `ObjectiveSuiteManifest`

V4 adds narrow blockedness and dependency annotations so derived channels can say:

- what can block a channel
- which channels depend on which others

This still does **not** make scalar reward public truth.

### `ObjectiveBreakdownResult`

V4 adds:

- signal-level status
- slice-level status

That makes package-level evidence inspectable without promoting search-time scalarization into the public surface.

## Slice-aware promotion and generalization

Once V4 has more than one real package lane, promotion cannot stay “package win therefore promote.”

Promotion has to stay explicit about:

- which typed transfer slices were actually covered
- which slices were blocked or inconclusive
- whether model-tier escalation happened
- whether the package claim stayed inside the declared applicability scope
- whether higher-risk wins include bounded member-family attribution

This remains evidence-first, not reward-first:

- slice status is public and inspectable
- applicability scope is public and inspectable
- promotion remains blocked when optimistic scope expansion appears
- higher-risk package wins remain frontier-only unless attribution and slice coverage are real

The key rule is simple:

> a package-level win only applies where the declared transfer slices, package scope, and model-tier audit actually support it.

## Reward boundary

BreadBoard still has two value-adjacent lanes:

- optimize’s evaluation/objective/comparison/promotion stack
- the separate reward aggregation and telemetry lane

V4 keeps the boundary explicit:

- evaluation remains truth
- objectives remain inspectable derived optimization structure
- reward-like scoring remains private search aid
- search/scalarization remains backend-private

That means:

- no public `RewardSuiteManifest`
- no reward-first optimization architecture
- no promotion on scalarized search score alone

## First V4 proving lane

The first V4 lane should stay on the current bounded dossier package and compose exactly 3 families:

- support / execution
- tool guidance
- coding overlay

This is the safest first package lane because it is:

- highly BreadBoard-native
- easy to interpret
- still Nano-valid
- rich enough to force package-level mixed evidence and transfer/applicability handling

## Second V4 package lane

The second V4 lane should stay equally bounded and move to a distinct E4 package rather than widening
the first dossier lane indefinitely.

The live proving target is OpenCode `1.2.17`, composed from:

- one prompt-pack family
- one bounded config family
- one tool-guidance / tool-pack family

This lane matters because it forces V4 to prove that mixed-evidence package optimization is not
just a Codex-dossier trick. It also gives the repo a clean place to exercise:

- typed package transfer slices
- tool-pack-specific applicability claims
- Nano-first experimentation with auditable Mini escalation
- promotion evidence that stays explicit about package scope

## Cost policy

V4 keeps the same doctrine:

- `GPT-5.4 Nano` is the default
- `GPT-5.4 Mini` is used only by explicit escalation policy

For tranche 1:

- triplet lane A stays Nano-only
- triplet lane B may escalate to Mini only after Nano on ambiguous or close-margin evidence
- any Mini path must remain explicit, auditable, and justified

That means:

- Nano remains the default search and comparison tier
- Mini is an escalation path, not the default proving path
- promotion evidence must record whether Mini was actually used
- backend-private search traces may react to Mini audit, but public optimize truth does not become search-policy truth

## What stays private

The following remain private:

- mixed-evidence scalarization
- blocked-component penalties
- uncertainty penalties
- Nano→Mini escalation logic
- candidate scheduling
- search-policy traces

In the current V4 tranche, private search policy now includes:

- transfer-slice-sensitive penalties
- blocked and inconclusive slice handling
- uncertainty penalties
- auditable Nano→Mini escalation decisions
- a private record of whether escalation changed the final stage winner

All of that stays backend-private.

The public optimize center remains:

- evaluation suites
- objective suites
- transfer slices
- comparison results
- promotion evidence

These can become more capable in V4, but they do not become public optimize truth.

## Optional narrow follow-on

The current V4 follow-on stays intentionally narrow.

It reuses the existing `VerifierAugmentedExperimentResult` shape on the bounded OpenCode package
lane instead of minting a second package-experiment ontology.

The live follow-on:

- starts from the existing `prompt + bounded config + tool-guidance/tool-pack` package candidate
- applies one verifier-assisted refinement inside already-declared package loci
- keeps transfer slices, package scope, and Nano-first policy explicit in metadata
- remains backend-only and non-kernel

This proves that package-scoped specialization can stay subordinate to:

- the declared evaluation and objective suites
- the declared family composition and search space
- the declared transfer/applicability slices

without reopening reward ontology, search-policy ontology, or DARWIN semantics.

## What V4 still does not do

V4 does **not** introduce:

- DARWIN campaign state
- archive / island / genealogy ontology
- public reward-suite ontology
- public search-policy ontology in tranche 1
- generic co-optimization DSLs
- 4+ family live lanes
- cross-package composition
- online self-tuning

The point of V4 is to prove mixed-evidence package lanes cleanly, not to reopen the whole optimizer architecture.
