# Optimization System Overview

BreadBoard’s optimization subsystem is the repository’s bounded improvement and evidence-governed mutation layer.

It is the answer to a very specific question:

How do you improve agent behaviors, overlays, support structures, and related package-scoped configurations without collapsing into black-box reward hacking, vague prompt iteration, or an outer-loop campaign system that should really live somewhere else?

The current optimization stack is the result of several rounds of pressure and restraint. The important thing is not that it gained many nouns. The important thing is that those nouns now fit together.

This overview explains that fit.

---

## The central idea

Optimization in BreadBoard is built around a strict split:

- public evaluation truth
- derived objective structure
- bounded search spaces
- typed family and transfer structure
- backend-private scalarization and ranking
- promotion and generalization claims that must be evidence-backed

That split sounds procedural, but it is the reason the subsystem is maintainable.

Without it, the system would quickly drift into one of two bad states:

- everything becomes a private reward function that no one can inspect
- or every useful internal heuristic becomes a public ontology surface

BreadBoard’s optimization work tries to avoid both.

---

## What the subsystem is for

Optimization is for bounded, typed improvement programs over mutable loci such as:

- prompt overlays
- tool-guidance overlays
- support execution scaffolds
- bounded config surfaces
- package-scoped triplets and cohorts
- runtime-context-sensitive variants

It is not a general evolutionary campaign system. It is not a training framework. It is not a public reward-ontology product.

It is the layer where BreadBoard can run disciplined improvement studies while keeping:

- evidence inspectable
- costs bounded
- transfer claims honest
- promotion gated by replay and support requirements

---

## The major architectural pieces

## 1. Substrate

The substrate defines the mutation-bearing objects:

- optimization targets
- mutable loci
- candidate bundles
- materialized candidates
- support envelopes
- invariants and artifact references

This is the “what can move?” layer.

It matters because most weak optimization systems never formalize their mutation boundary. BreadBoard does. That makes it possible to ask:

- what changed
- what was kept fixed
- what support assumptions traveled with the candidate
- whether the candidate remains bounded and valid

## 2. Dataset and evaluation truth

Optimization samples, datasets, ground truth packages, correctness rationales, evaluation records, and diagnostic bundles provide the public truth layer.

This is where the system insists on answering:

- what problem instance was being judged
- what correctness criteria actually matter
- what went wrong
- how much of the judgment is grounded versus inferred

The evaluation layer is not decorative. It is the truth anchor that stops the rest of the subsystem from drifting into arbitrary search.

## 3. Objectives and benchmarks

Once evaluation truth exists, objective suites and benchmark structures can derive inspectable channels from it.

This is where BreadBoard gained the ability to talk about:

- objective breakdowns
- benchmark splits
- candidate comparisons
- benchmark run manifests and results
- backend comparison results

The key doctrine is that objectives are derived from evaluation truth. They are not allowed to silently replace it.

## 4. Search spaces and target families

Target families and search-space manifests are where the system stopped being “mutate one thing and see.”

They let BreadBoard model:

- bounded family-level optimization programs
- family-specific legal mutation surfaces
- cross-family constraints
- package- or lane-level proving setups

This is where the optimizer became structurally meaningful rather than a bag of local mutation policies.

## 5. Composition, transfer, and cohorts

Later rounds added the structures needed to move beyond isolated single-family work:

- `FamilyCompositionManifest`
- `TransferSliceManifest`
- `TransferCohortManifest`

These exist because real improvement pressure often lives at the boundary between local wins and honest generalization claims.

BreadBoard now has typed ways to say:

- these families were composed together structurally
- these transfer slices define applicability/evidence boundaries
- this cohort groups bounded package claims together

That is much better than letting transfer and generalization remain vague narrative claims.

## 6. Promotion and generalization evidence

Promotion is where the subsystem becomes serious.

Candidates do not simply “win.” They move through:

- replay and conformance gates
- support-envelope gates
- family and slice-aware promotion gates
- claim-tier checks
- bounded applicability summaries

This gives BreadBoard a structured answer to:

- what counts as ready
- what remains frontier-only
- what is package-local
- what is transfer-supported
- what is cohort-supported

The presence of explicit promotion evidence and summary objects is one of the subsystem’s strongest design features.

## 7. Backend-private search and live experiment policy

The reflective backends, mutation/reflection policies, and private search logic are where actual exploration happens.

Crucially, those internals are not treated as public truth.

That lets the system benefit from:

- scalarization
- portfolio policies
- blockedness penalties
- uncertainty handling
- Nano/Mini escalation policies

without forcing every internal heuristic into a stable public contract.

That separation is the only reason the subsystem can still evolve without destabilizing everything else.

---

## The developmental arc

One useful way to understand the subsystem is to see what each major round added.

### V1

The substrate, dataset, evaluation, backend, promotion, and runtime-context layers established the optimizer as a real typed system.

### V1.5

Evidence integration and candidate comparison machinery made the optimizer more benchmark- and support-aware.

### V2

Evaluation suites, objective suites, target families, and search spaces turned it into a family-structured optimization framework.

### V3

Composed families and private search-policy discipline handled broader but still bounded structural optimization.

### V4

Transfer slices and mixed-evidence package lanes moved the center of gravity toward honest package-scoped triplet optimization.

### V5

Transfer cohorts and claim tiers let the system reason about bounded multi-package generalization evidence.

### V6

Live experiment cells and the explicit kernel freeze clarified that the optimizer had become mature enough to stop adding public structure by default and start proving itself under use.

That arc is worth noticing because it shows a rare thing in research infrastructure: the subsystem got more capable while simultaneously learning where to stop.

---

## The most important doctrines

## Evaluation is truth

This is the subsystem’s foundational discipline.

Everything else can be clever. Evaluation truth cannot be casual.

## Objectives are derived and inspectable

Objective structure matters, but it should remain readable and auditable.

## Reward-like ranking stays private

This prevents public-ontology bloat and keeps experimental heuristics flexible.

## Transfer claims must be typed and bounded

The subsystem explicitly rejects vague “it generalized” storytelling.

## Nano-first experimentation

The later rounds made compute discipline first-class. Expensive escalation exists, but it must be justified and auditable.

## Promotion is not a vibe

Support evidence, replay, conformance, and claim tiers all have to line up.

---

## Where optimization fits in the larger BreadBoard architecture

Optimization sits between runtime/search truth and broader outer-loop research systems.

### Upstream inputs

It consumes:

- datasets and evaluation records
- runtime-context constraints
- benchmark structure
- sometimes adapter-friendly outputs from DAG or related systems

### Downstream relationships

It can feed:

- package and cohort evidence
- promotion and frontier decisions
- later outer-loop campaign systems such as DARWIN

### What it is not supposed to absorb

Optimization should not absorb:

- DAG runtime semantics
- RL export ontology
- DARWIN campaign management
- broad archive/island/genealogy semantics

That boundary is explicit and healthy.

---

## Typical use cases

Use the optimization subsystem when you want to:

- improve prompt/config/tool-guidance/support combinations on a bounded target package
- compare candidate variants under structured benchmark splits
- reason about package-local versus transfer-supported gains
- model family composition without collapsing everything into a general evolution program
- keep live experiment compute and promotion claims disciplined

Do not reach for optimization first if the problem is primarily:

- DAG-style branch/search execution geometry
- RL export and trainer integration
- outer-loop campaign and policy orchestration

Those belong elsewhere.

---

## Recommended reading after this overview

For the exact subsystem arc, read the tranche docs in order:

- [Optimization V1 substrate](optimization-v1-substrate.md)
- [Optimization V1 datasets](optimization-v1-datasets.md)
- [Optimization V1 evaluation](optimization-v1-evaluation.md)
- [Optimization V1 backend](optimization-v1-backend.md)
- [Optimization V1 promotion](optimization-v1-promotion.md)
- [Optimization V1 runtime context](optimization-v1-runtime-context.md)
- [Optimization V1.5 evidence integration](optimization-v1-5-evidence-integration.md)
- [Optimization V2 suites and target families](optimization-v2-suites-and-target-families.md)
- [Optimization V3 composed families and search policy](optimization-v3-composed-families-and-search-policy.md)
- [Optimization V4 mixed-evidence package lanes](optimization-v4-mixed-evidence-package-lanes.md)
- [Optimization V5 transfer cohorts and generalization evidence](optimization-v5-transfer-cohorts-and-generalization-evidence.md)
- [Optimization V6 live experiment cells and kernel freeze](optimization-v6-live-experiment-cells-and-kernel-freeze.md)
- [Optimization V6 stop/go synthesis](optimization-v6-stop-go-synthesis.md)

---

## Final perspective

BreadBoard’s optimization subsystem matters because it proves that a GEPA-like research direction can be pursued without surrendering structure.

The system now supports:

- meaningful mutation and reflection
- typed evaluation and comparison
- family and transfer structure
- honest generalization claims
- live experiment discipline

without turning the repository into an uninspectable optimization lab.

That combination of ambition and restraint is the real accomplishment.
