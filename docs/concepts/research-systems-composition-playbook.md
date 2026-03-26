# Research Systems Composition Playbook

This document is the practical companion to the broader research-systems overview.

The overview explains what each subsystem is. This playbook answers a different question:

How do you actually combine them without making the repository harder to understand?

That question matters because BreadBoard now has several serious research-facing layers:

- ATP and sandbox contracts
- optimization
- DAG runtime
- RL overlays
- C-Trees
- DARWIN

The danger at this stage is not lack of capability. The danger is composition drift: using the right parts together for the wrong reasons, or letting one subsystem absorb another’s responsibilities.

This guide is meant to prevent that.

---

## The composition rule of thumb

Before combining systems, ask:

1. What is the canonical truth surface here?
2. Which layer is allowed to add derived structure?
3. Which layer is allowed to make decisions?
4. Which layer is only allowed to consume and package evidence?

If those answers are not clear, the composition is probably wrong.

BreadBoard’s best subsystem work follows this pattern:

- one layer owns truth
- another may own derived helper structure
- another may own bounded decision logic
- another may own outer-loop governance

When two layers both think they own the same thing, the result usually gets muddy fast.

---

## Pattern 1: ATP plus sandboxing

This is the cleanest of the research-system compositions.

### Use this pattern when

You have a formal reasoning or proving workload that needs:

- retrieval and decomposition logic
- specialized solver fallbacks
- normalized diagnostics
- bounded repair decisions
- isolated or reproducible execution

### Recommended composition

- ATP owns:
  - retrieval requests/responses
  - solver requests/responses/traces
  - decomposition events and traces
  - proof plan IR
  - diagnostics and repair policy
- sandboxing owns:
  - the execution request/result envelope
  - placement, image/rootfs, and evidence-facing execution outcome

### Keep out of ATP

- backend-specific container or microVM semantics
- broad host/runtime execution policy

### Keep out of sandboxing

- prover-specific reasoning semantics
- repair-policy meaning

### Why this composition works

It preserves the distinction between:

- reasoning loop truth
- execution truth

That is what makes theorem-proving loops auditable instead of opaque.

---

## Pattern 2: DAG runtime plus optimization

This is one of the most useful current composition paths.

### Use this pattern when

You need search-shaped execution or adjudication, and you also want to compare, benchmark, or promote variants through optimization-style evidence.

Typical cases:

- verifier-guided patch search
- judge/reducer narrowing before candidate comparison
- branch execute/verify programs whose evidence should become benchmark input

### Recommended composition

- DAG owns:
  - search runs, frontiers, candidates, branches, messages
  - assessments
  - search trajectories
- optimization owns:
  - benchmark manifests and result structures
  - candidate comparisons
  - objective and promotion evidence
  - package, transfer, and cohort claims

### The adapter boundary

The clean boundary is:

- DAG emits structured evidence
- optimization consumes it through bounded adapter/export surfaces

Do not pull optimization nouns into DAG runtime truth just because the outputs are useful downstream.

### Why this composition works

It lets search remain search and optimization remain optimization.

The moment DAG starts speaking in promotion or cohort ontology, or optimization starts owning branch/search runtime semantics, the boundary gets much harder to maintain.

---

## Pattern 3: DAG runtime plus RL overlay

This is the most important training-facing composition in the repository right now.

### Use this pattern when

You want to turn search-shaped execution into training-ready graphs, dataset units, or conformance/fidelity studies.

Typical cases:

- assessment-bearing trajectories for offline study
- verifier-rich examples for downstream training
- replay/live parity checks over search-derived export bundles

### Recommended composition

- DAG owns:
  - search truth
  - assessment truth
  - branch/message/workspace semantics
- RL owns:
  - trajectory graph projection
  - export manifests and fingerprints
  - conformance packets
  - adapter probe evidence

### Why this composition works

The RL layer projects from the runtime truth instead of reinventing it.

That gives BreadBoard:

- graph-native training data
- replay/live parity
- export fidelity

without creating an RL-specific search runtime.

### The main mistake to avoid

Do not let RL become the place where search semantics are redefined. If a search concept matters, it should usually exist in DAG or runtime truth first.

---

## Pattern 4: optimization plus DARWIN

This composition becomes relevant once bounded improvement work starts to look like a program instead of a slice.

### Use this pattern when

You have multiple bounded optimization efforts and you need:

- lane structure
- policy bundles
- evidence and claim packaging
- decision tracking over time

### Recommended composition

- optimization owns:
  - bounded mutation and reflection
  - benchmark/evaluation/objective truth
  - promotion and generalization evidence
- DARWIN owns:
  - campaign specs
  - lane registries
  - policy bundles
  - decision records
  - evolution ledgers
  - claim/evidence packaging across broader programs

### The main mistake to avoid

Do not use DARWIN to compensate for an optimizer that has weak internal evidence discipline. DARWIN should sit above a strong optimization substrate, not replace it.

---

## Pattern 5: C-Trees plus benchmark and evaluation machinery

C-Trees are the least “fully documented” subsystem, so this composition benefits from extra clarity.

### Use this pattern when

You are studying tree-structured reasoning/control quality and need:

- branch-receipt or closure discipline
- verifier/executor coupling
- historical versus live baseline comparisons
- downstream task-pack or holdout generalization studies

### Recommended composition

- C-Trees own:
  - tree structure and tree-specific policy
  - branch receipts
  - finish closure and rehydration behavior
  - runtime state and tree-control phases
- benchmark/evaluation systems own:
  - comparative evaluation protocols
  - holdout and baseline summaries
  - claim-bearing scoring outside the tree runtime itself

### Why this matters

C-Trees are powerful precisely because they are not “just DAG with more stuff.” Preserving that distinction helps the research stay interpretable.

---

## Pattern 6: ATP plus DAG

This is a more specialized composition, but it is a plausible one.

### Use this pattern when

The formal proving loop itself has a search geometry:

- multiple proof branches
- adjudicated solver paths
- verifier-gated narrowing
- decomposition paths that should be explicit and replayable

### Recommended composition

- ATP owns:
  - domain semantics of the formal loop
  - proof-oriented diagnostics, repair, and solver behavior
- DAG owns:
  - branch and adjudication structure
  - frontier behavior
  - assessment-bearing search geometry

### What to avoid

Do not let ATP become a generic search runtime. Do not let DAG become a theorem-proving ontology.

That line is exactly what keeps the composition reusable.

---

## Pattern 7: RL plus adapter probes plus real training stacks

This is the current “use under pressure” path for the RL subsystem.

### Use this pattern when

You need to prove that BreadBoard’s training-facing exports are useful and faithful in downstream systems.

### Recommended composition

- RL owns:
  - trajectory projection
  - export manifests
  - evaluation packs
  - conformance and probe reports
- downstream trainer or data systems own:
  - packing, sharding, tensorization, sampling, and training logic

### Why this composition works

It lets BreadBoard stop at the point where it can make strong claims about truth and fidelity, without turning into a full training platform.

---

## Anti-patterns

These are the main composition mistakes worth watching for.

## Anti-pattern 1: one layer starts speaking everyone else’s ontology

Example:

- DAG starts emitting cohort and promotion semantics
- RL starts defining search runtime behavior
- DARWIN starts acting like a local scheduler

This usually means the composition boundary is wrong.

## Anti-pattern 2: helper layers quietly become kernel truth

Several subsystems now have helper or fidelity layers. That is healthy. What would be unhealthy is quietly treating those helper artifacts as if they were new kernel semantics.

## Anti-pattern 3: campaign pressure leaks into inner-loop runtimes

If a problem is really about lanes, claims, or weekly evidence packets, it probably belongs in DARWIN, not in DAG or optimization runtime truth.

## Anti-pattern 4: training concerns rewrite runtime truth

If a trainer needs a new projection or export slice, the first move should usually be a helper or overlay change, not a runtime-kernel mutation.

---

## Choosing the right subsystem first

When a new research idea arrives, a useful checklist is:

### Start with ATP if

The central difficulty is formal reasoning, proof diagnostics, solver routing, or formal repair.

### Start with DAG if

The central difficulty is branch/search/adjudication geometry.

### Start with optimization if

The central difficulty is bounded mutation, comparison, and evidence-gated promotion.

### Start with RL if

The central difficulty is training-facing graph projection, export fidelity, replay parity, or adapter proof.

### Start with C-Trees if

The central difficulty is structured tree control, closure, rehydration, or branch-receipt logic.

### Start with DARWIN if

The central difficulty is campaign, lane, policy, evidence, or long-horizon decision governance.

That single choice often prevents weeks of architectural muddle.

---

## Final perspective

BreadBoard’s recent subsystem growth only remains healthy if composition stays disciplined.

The good news is that the repository now has strong boundaries to compose around:

- ATP for formal loop semantics
- sandboxing for execution envelopes
- DAG for search-shaped runtime truth
- optimization for bounded improvement programs
- RL for training-facing projection and conformance
- C-Trees for richer tree-control research
- DARWIN for outer-loop evolutionary governance

This playbook is really a reminder of that fact: the right next move is often not “build a new subsystem,” but “compose the existing ones cleanly and let the boundaries do their job.”
