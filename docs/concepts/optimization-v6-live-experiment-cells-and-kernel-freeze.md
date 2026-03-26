# Optimization V6 Live Experiment Cells and Kernel Freeze

Optimization V6 starts where V5 stops.

V5 made transfer cohorts and claim tiers real:

- `TransferCohortManifest`
- package-local / transfer-supported / cohort-supported claims
- cohort-aware promotion evidence
- backend-private cohort-aware ranking and stopping
- bounded verifier-assisted follow-ons

What V5 did **not** solve is the next pressure:

> how to use the platform under real repeated live experiment pressure without drifting into new ontology, reward-led truth, or DARWIN-lite study management.

## Kernel freeze

V6 explicitly freezes the current public optimize kernel surface.

That frozen public center is:

- `EvaluationSuiteManifest`
- `ObjectiveSuiteManifest`
- `ObjectiveBreakdownResult`
- `TargetFamilyManifest`
- `SearchSpaceManifest`
- `FamilyCompositionManifest`
- `TransferSliceManifest`
- `TransferCohortManifest`
- benchmark, comparison, and promotion artifacts
- typed claim tiers

V6 adds **no new public optimize artifact**.

If a future round is justified, it must be justified by repeated live-study shape pressure, not by planner imagination.

## Value seam

V6 freezes the optimize ↔ reward doctrine:

- evaluation is truth
- objective suites are derived and inspectable
- reward-like ranking stays private search or telemetry
- promotion never rests on scalarized search scores alone

This keeps optimize from drifting into a second public value language.

## Public vs private

The public/private line is now explicit.

Public and stable enough for bounded research use:

- suites, families, compositions, slices, cohorts
- benchmark manifests/results
- comparison results
- promotion evidence and claim tiers

Private and intentionally non-public:

- scalarization
- reward-like rollups
- candidate scheduling
- tie-break logic
- Mini escalation policy
- attribution and ablation heuristics
- early stopping heuristics
- blockedness and uncertainty penalties

Search traces may be exposed for audit, but that does **not** make search policy public optimize truth.

## Live experiment cell doctrine

The right V6 unit of experiment is the existing **cohort cell**:

- one `TransferCohortManifest`
- one family or composition
- one evaluation suite
- one objective suite
- one backend/search policy
- one model-tier policy
- one fixed split set
- one bounded budget

V6 does **not** introduce a public `ExperimentCellManifest`.

## First proving ground

The first live pair remains:

- `codex_dossier.current`
- `opencode_1_2_17.current`

### Cell A

Shared emphasis:

- tool-guidance / tool-pack clarity
- bounded edit / support honesty

Model policy:

- Nano-only

Baselines:

- atomic sequential
- V4 local package candidates
- V5 cohort-aware staged baseline

### Cell B

Same pair, harder emphasis:

- replay-safe prompt/config coherence
- package-scope integrity

Model policy:

- Nano-first
- Mini only as audited escalation on shortlisted ambiguous candidates
- verifier follow-on only on the top slice

## Nano/Mini doctrine

V6 keeps the existing policy and makes it explicit under live use:

- `GPT-5.4 Nano` is default
- `GPT-5.4 Mini` is escalation only

Nano-only classes:

- support/execution families
- tool-guidance-only cells
- deterministic reruns
- comparison bookkeeping
- objective aggregation
- early cohort sweeps

Nano→Mini escalation is allowed only for:

- ambiguous semantic package-pair cells
- bounded verifier-assisted repair on shortlisted candidates

Never spend Mini on:

- full cohort sweeps
- deterministic evaluators
- routine comparisons
- objective aggregation
- low-risk wording sweeps
- backend shootouts

## Fairness and contamination rules

V6 live cells keep the following non-negotiable:

- keep proposal tier fixed within the cell
- use the same escalation triggers inside the same comparison
- reevaluate parent and child under the same tier when escalation triggers
- report Nano-only and escalated outcomes separately
- do not compare one backend on Nano to another on Mini and call it a backend comparison
- do not leak hidden cohort/package identity into mutation time
- do not reuse gold rationales across packages outside allowed splits

## What live results are allowed to change

V6 exists partly to make this boundary explicit.

Possible outcomes of a live study:

- experiment-only result
- private heuristic candidate
- durable backend-private heuristic
- durable doctrine/default change

V6 does **not** assume that a live win automatically changes stable behavior.

## What not to build next

V6 explicitly refuses:

- public `RewardSuiteManifest`
- public `SearchPolicyManifest`
- package-graph or package-set ontology
- study-manager or campaign artifacts inside optimize
- 4+ family widening as the next center
- backend-shopping rounds
- optimize ↔ DARWIN blur
- optimize ↔ DAG-runtime coupling

The most seductive wrong move here is turning repeated multi-package evidence into a study-manager ontology. That is DARWIN pressure, not V6 optimize pressure.

## Stop condition

The intended end-state of V6 is not “invent one more artifact.”

The intended end-state is:

- live cohort cells run on the current platform
- no new public artifact is needed
- claim tiers remain honest
- Mini remains rare and auditable
- the main bottleneck becomes choosing better studies, not extending the kernel

If that is where V6 lands, the correct next step is to use the platform, not plan V7 by default.
