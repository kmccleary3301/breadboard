# DARWIN System Overview

DARWIN is BreadBoard’s outer-loop evolutionary program layer.

That description matters because DARWIN is easy to misunderstand if you only notice words like “candidate,” “evaluation,” or “policy.” Optimization, DAG, and RL all contain objects with related names. DARWIN is different because it operates one layer out:

- it organizes campaigns rather than local bounded mutations
- it manages lanes and policy bundles rather than one search run
- it packages evidence and claims rather than only raw runtime truth
- it records decisions and evolution over time rather than just one experiment packet

This document is the readable map for that layer.

---

## The central idea

DARWIN is for the point where BreadBoard stops asking:

- “which candidate wins this bounded local comparison?”

and starts asking:

- “how should a longer-horizon research or evolutionary program be structured, governed, evaluated, and claimed?”

That is why DARWIN has its own contract families.

It is not just a bigger optimizer. It is a different scale of system.

---

## The artifact-centered design

The current DARWIN surface is best understood through its typed artifacts.

The contract family already includes validators for:

- campaign specs
- policy bundles
- candidate artifacts
- evaluation records
- evidence bundles
- claim records
- lane registries
- policy registries
- weekly evidence packets
- effective config
- execution plans
- effective policy
- evaluator packs
- component references
- decision records
- evolution ledgers

That list tells you almost everything important about DARWIN.

It is:

- campaign-aware
- lane-aware
- policy-aware
- claim-aware
- ledger-aware

This is a program-management and evidence-governance layer for evolutionary work, not merely another execution substrate.

---

## Why DARWIN exists separately

BreadBoard already has:

- the kernel for runtime truth
- ATP for theorem-proving and repair contracts
- optimization for bounded improvement programs
- DAG for search-shaped control and replication
- RL for training-facing data projection
- C-Trees for richer tree-structured control research

So why DARWIN?

Because none of those subsystems should be forced to absorb the responsibilities of:

- campaign scoping
- lane configuration
- policy bundling and registry management
- claim packaging
- evidence bundle governance
- longer-horizon evolution history

If those concerns were pushed down into optimize or DAG, those systems would become much less maintainable.

DARWIN exists to keep that outer-loop responsibility in its own layer.

---

## The major concepts

## Campaigns

A DARWIN campaign is the broad research or evolution program boundary.

It defines the scope in which:

- lanes exist
- policies are active
- candidates are produced
- evaluations are gathered
- evidence is bundled
- claims are made

Campaigns are the answer to “what program are we actually running?”

## Lanes

Lanes are DARWIN’s bounded internal tracks.

They let the outer-loop system separate different kinds of effort or evaluation paths without collapsing them into one undifferentiated search space.

This is especially useful when different candidate families or problem domains require different:

- policies
- evaluator mixes
- economic assumptions
- decision thresholds

## Policies and registries

DARWIN is explicit about policy because outer-loop experimentation becomes incoherent if policy is only inferred from behavior.

Policy bundles, effective policies, and registries provide the governance story for:

- what rules were active
- how lane behavior was constrained
- which policy version governed a decision

That is a major maturity signal. Serious evolutionary programs need policy traceability.

## Candidates, evaluations, evidence, and claims

These are the heart of DARWIN’s evidence stack.

Candidate artifacts and evaluation records capture what was tested.

Evidence bundles and weekly packets organize what was learned.

Claim records make the outer-loop system explicit about what it believes it can say publicly or internally.

This is the part of DARWIN that keeps “research progress” from degenerating into scattered experiment notes.

## Decisions and evolution ledgers

Decision records and evolution ledgers give DARWIN temporal memory.

They answer questions like:

- why did the program change direction?
- what policy or evidence justified that change?
- what lineage of decisions led here?

That is what makes DARWIN a program layer rather than a collection of scripts.

---

## How DARWIN differs from optimization

This is the most important boundary to keep clear.

Optimization is for bounded improvement programs:

- typed loci
- evaluation truth
- objectives and benchmarks
- transfer/generalization claims
- promotion and frontier decisions

DARWIN is for outer-loop research governance:

- campaign specs
- lanes
- policies
- evidence and claim packaging
- long-horizon decision tracking

Optimization asks: “how do we improve this bounded thing honestly?”

DARWIN asks: “how do we run and govern a broader evolutionary research program over time?”

Those are not the same problem.

---

## How DARWIN differs from DAG runtime

DAG is the inner-loop runtime for search-shaped recipes.

It owns:

- frontier structure
- assessments
- branch/message logic
- search trajectories
- replication helper packets

DARWIN should not become a search runtime.

Its role begins once multiple runs, lanes, policies, and evidence packets need to be coordinated at a higher level.

In other words:

- DAG is about one search-shaped method or recipe
- DARWIN is about a broader evolutionary program that may consume evidence from many such methods

---

## How DARWIN differs from RL

RL is about graph-native trajectory truth, export fidelity, and training-facing adapters.

DARWIN is about campaign and evidence governance over longer horizons.

DARWIN may consume evaluation or dataset-derived evidence, but it should not become the place where training/export semantics are invented.

That division keeps RL narrow and DARWIN strategic.

---

## Typical DARWIN use cases

Reach for DARWIN when you need:

- a campaign spec and lane structure
- policy bundle governance
- explicit evidence and claim packaging
- evolutionary decision records across multiple bounded efforts
- weekly or tranche-level evidence synthesis
- a ledger of how a broader research program evolved

Do not start with DARWIN if your problem is still:

- a single bounded optimizer loop
- one search runtime recipe
- one RL export path
- or one proving slice

Those problems should stay lower in the stack until they create true outer-loop pressure.

---

## Why the contract-heavy design is healthy

DARWIN could have easily become a loose pile of status docs and orchestration scripts.

Instead, the repo now contains a strong typed artifact surface around the outer-loop layer. That buys:

- interpretability
- claim discipline
- policy traceability
- compatibility checking
- easier integration with the rest of the repository

This is especially important because outer-loop systems are where architectural drift tends to hide.

By forcing campaigns, lanes, policies, evidence, claims, and decisions into explicit artifacts, DARWIN remains far more legible than most research-orchestration layers usually are.

---

## Recommended reading after this overview

If you want the exact contract layer behind DARWIN:

- browse the DARWIN schemas under [docs/contracts/darwin](../contracts/darwin)
- inspect the validator surface in [breadboard_ext/darwin/contracts.py](../../breadboard_ext/darwin/contracts.py)

For stage-specific notes, the repository also contains a large DARWIN document set outside this overview, including:

- execution plans
- tranche notes
- stability reviews
- route-mix notes
- claim boundary notes
- signoff and roadmap handoff docs

Those are useful once you already understand the artifact model.

---

## Final perspective

DARWIN is the system that keeps BreadBoard’s broader evolutionary ambitions from leaking into every other subsystem.

It gives the repository a place for:

- campaigns
- lanes
- policy bundles
- evidence programs
- claims
- decisions
- evolution history

without forcing optimization, DAG, RL, or ATP to become something they are not.

That separation is a major architectural advantage. It means BreadBoard can grow large research programs without losing the clarity of its inner-loop substrates.
