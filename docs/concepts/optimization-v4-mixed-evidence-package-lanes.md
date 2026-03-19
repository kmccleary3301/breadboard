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

## Cost policy

V4 keeps the same doctrine:

- `GPT-5.4 Nano` is the default
- `GPT-5.4 Mini` is used only by explicit escalation policy

For tranche 1:

- triplet lane A stays Nano-only
- any Mini path must remain separate, auditable, and justified

## What stays private

The following remain private:

- mixed-evidence scalarization
- blocked-component penalties
- uncertainty penalties
- Nano→Mini escalation logic
- candidate scheduling
- search-policy traces

These can become more capable in V4, but they do not become public optimize truth.

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
