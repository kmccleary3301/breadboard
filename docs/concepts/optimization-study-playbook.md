# Optimization Study Playbook

This playbook is for engineers and researchers who already understand the optimization subsystem in principle and now need to use it well.

The optimization overview explains the architecture. This document is about practice:

- how to scope a study
- how to choose the right optimization layer
- how to keep evidence and claims honest
- how to compose optimization with DAG, RL, and DARWIN without making the boundaries worse

BreadBoard’s optimization stack is powerful enough now that the main failure mode is no longer “not enough primitives.” It is “using the right primitives sloppily.”

This guide is meant to prevent that.

---

## Start with the right question

Before opening the optimization package, decide which question you are actually asking.

### Question A: “Can we improve this bounded candidate surface?”

This is the classic optimization question.

Reach for:

- substrate primitives
- evaluation and diagnostic records
- objective suites
- target families
- bounded search spaces
- promotion gates

### Question B: “Can we make honest package-level or cross-package claims?”

This is the transfer/generalization question.

Reach for:

- family composition
- transfer slices
- transfer cohorts
- claim tiers
- slice-aware or cohort-aware promotion evidence

### Question C: “Can we run live studies under real cost pressure?”

This is the experiment-cell question.

Reach for:

- V6 live cohort-cell doctrine
- Nano/Mini fairness rules
- bounded private search traces
- explicit stop/go synthesis

If you do not know which question you are asking, the study is underspecified.

---

## A clean study lifecycle

Most high-quality optimization work in BreadBoard should follow this order.

## 1. Define the target and bounded loci

Start with:

- `OptimizationTarget`
- `MutableLocus`
- invariants
- support-envelope assumptions

Ask:

- what is allowed to move?
- what must remain fixed?
- what support assumptions are part of the candidate?
- what would make the candidate invalid even before evaluation?

If this is fuzzy, everything downstream gets worse.

## 2. Define evaluation truth before objective cleverness

Create or choose:

- dataset/sample structure
- correctness rationale
- evaluation records
- diagnostic bundle expectations

This is the place to be conservative. If evaluation truth is weak, no amount of objective design will save the study.

## 3. Choose objective and benchmark structure

Only after the evaluation layer is stable should you define:

- benchmark splits
- benchmark manifests
- candidate comparison rules
- objective suites and breakdowns

This is where BreadBoard’s optimization line is strongest. It lets the study remain explicit about:

- what is being measured
- what is visible to which split
- what is a comparison result versus a raw evaluation

## 4. Bind the study to a family or package shape

At this point you should know whether the work is:

- single-family
- family-composed
- package-lane
- transfer-slice
- transfer-cohort

Do not widen this later by storytelling. Choose the right study shape up front.

## 5. Run the bounded backend

Now the backends and private reflection/search logic can do their job.

The point of all the earlier structure is that backend cleverness is now constrained by:

- bounded loci
- evaluation truth
- benchmark structure
- family or package shape
- support-envelope discipline

This is the difference between a research substrate and a “self-improve” folklore loop.

## 6. Promote carefully

A study is not finished when a candidate appears to do well.

It is finished when you know whether the result is:

- frontier-only
- package-local
- transfer-supported
- cohort-supported
- or blocked from promotion

That is why promotion evidence exists.

---

## Choosing the right optimization level

One of the easiest ways to create confusion is to use a more advanced layer than the problem warrants.

### Use substrate-only work when

You are still proving out:

- a mutable locus
- evaluation structure
- simple backend reflection
- basic promotion criteria

### Use target families when

The study is really about a bounded class of related optimization targets with shared constraints.

### Use family composition when

The pressure is structural and you need multiple families together, but still in a clearly bounded configuration.

### Use transfer slices when

The missing pressure is applicability and evidence boundaries, not more family structure.

### Use transfer cohorts when

You are now making bounded multi-package or cross-package generalization claims and need an explicit claim layer.

### Use live cohort cells when

The question is no longer only structural but experimental:

- what happens under real model costs
- whether Nano-first is enough
- where escalation is justified

Choosing too high a level too early is one of the most common causes of optimization ontology sprawl.

---

## The evidence discipline you should not relax

The optimization subsystem works because it is unusually strict about evidence.

Do not relax these rules in a study just because the result seems promising.

## Evaluation remains truth

If a study starts leaning more on private ranking or summary language than on evaluation records, stop and repair the study.

## Objectives are derived, not magical

Objective suites are useful because they are inspectable. Do not turn them into a hidden reward layer.

## Reward-like search stays private

This is a feature. It allows experimentation without turning every heuristic into stable public ontology.

## Promotion claims must be bounded

If a result is only package-local, call it package-local.

If it is transfer-supported, the transfer slices need to say what that means.

If it is cohort-supported, the cohort artifacts need to justify that claim.

---

## How to choose between local, package, and cohort studies

### Local study

Choose this when you are still proving a bounded candidate mutation or evaluation setup.

### Package-lane study

Choose this when the work is meaningfully package-scoped and mixed-evidence, especially when multiple member families interact in one package lane.

### Cohort study

Choose this when the real question is whether an improvement pattern survives across a bounded set of packages under typed claim tiers.

Do not skip directly to cohorts because they sound more advanced. They are the right tool only when the generalization question is real.

---

## How optimization should compose with DAG

This is now a very important practical boundary.

Use DAG when the upstream method is search-shaped:

- branching
- adjudication
- verifier gating
- search trajectories

Use optimization when the downstream question is:

- benchmark comparison
- package-lane improvement
- transfer/generalization claims
- promotion and gating

The clean path is:

- DAG produces structured evidence
- optimization consumes it through bounded adapter surfaces

Do not import DAG runtime nouns into the optimization kernel.

---

## How optimization should compose with RL

Optimization should not become the training-data packaging system.

If a study needs:

- graph-native trajectory export
- replay/live fidelity
- adapter probe evidence

that belongs in the RL layer.

Optimization can later consume the resulting evidence or bounded packets if the study requires it. But the export and conformance semantics should remain where they belong.

---

## How optimization should compose with DARWIN

A useful rule of thumb is:

- if the question is still “which bounded candidate should advance?” stay in optimization
- if the question has become “how should this larger program be lane-, policy-, and claim-governed over time?” move to DARWIN

Optimization is not improved by acting like a campaign manager.

DARWIN is not improved by pretending it is a local mutation backend.

---

## Common failure modes

## Failure mode 1: vague mutation boundaries

If the mutable loci are unclear, the resulting comparisons will be hard to trust.

## Failure mode 2: objective-first, evaluation-later

This often produces attractive but brittle studies.

## Failure mode 3: unearned generalization language

This is exactly why transfer slices, cohorts, and claim tiers exist. Use them honestly.

## Failure mode 4: backend heuristics leaking into public doctrine

Private search should stay private unless repeated pressure proves a new helper surface is genuinely needed.

## Failure mode 5: skipping promotion discipline

A candidate that “looks good” is not the same as a candidate that can be promoted.

---

## Practical reading order when starting a real study

Use this path:

1. [Optimization System Overview](optimization-system-overview.md)
2. the relevant V1 tranche docs for substrate, evaluation, backend, and promotion
3. the family/composition/transfer tranche docs that match the study shape
4. the V6 live experiment docs if the study is going to touch real repeated experiments

If DAG-derived evidence is involved, read:

- [DAG Runtime System Overview](dag-runtime-system-overview.md)
- [DAG Runtime V2 optimize adapter note](dag-runtime-v2-optimize-adapter-note.md)

---

## Final perspective

The optimization subsystem is one of the strongest parts of BreadBoard now, but that strength depends on using it with discipline.

The best optimization studies in this repo will feel:

- bounded
- typed
- evidence-first
- cost-aware
- honest about transfer and promotion

When a study starts feeling like a giant untyped research blob, that is usually a sign it is no longer using the subsystem the way it was designed.
