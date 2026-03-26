# BreadBoard Research Systems Overview

BreadBoard now contains several substantial research-facing subsystems that sit above the core kernel and host/runtime contracts. They were not added as one monolithic framework. They emerged as a set of narrowly justified layers: theorem-proving and sandbox contracts, optimization primitives, DAG-shaped search/runtime modeling, RL-facing export and fidelity overlays, C-Trees research machinery, and DARWIN as the outer-loop evolutionary program layer.

This document is the readable map for that territory.

It is intentionally not the lowest-level source of truth. The kernel semantics, ATP schemas, DARWIN schemas, optimization tranche notes, DAG tranche notes, and RL tranche notes remain the precise contract documents. This page is the orientation layer that explains what these systems are for, how they relate, and how to approach them without getting lost.

---

## Why these systems exist

BreadBoard is trying to do something stricter than “agent framework plus experiments.”

The long-term aim is to support:

- reproducible task execution and replay
- rich multi-agent and multi-tool control flows
- theorem-proving and formal reasoning loops
- optimizer-style iterative improvement programs
- DAG-shaped search and adjudication recipes inspired by recent papers
- RL-compatible trajectory and dataset generation
- broader outer-loop evolution programs that operate over candidate systems, evidence, and policy

Those goals create conflicting pressures. A system can become expressive but unreadable. It can support experiments but lose stable truth surfaces. It can add specialized research modes and then become impossible to maintain.

The design response across these newer subsystems has been consistent:

- keep kernel truth narrow
- add typed helper or overlay layers only when repeated pressure forces them
- separate runtime truth from evaluation truth and from search/ranking heuristics
- keep outer-loop orchestration out of inner-loop runtimes
- prefer reusable primitives over paper-shaped modes

That doctrine matters more than any individual subsystem.

---

## The stack at a glance

The easiest way to understand the current architecture is as a layered map.

### 1. Kernel and host/runtime contracts

This is the base BreadBoard layer:

- task and subagent semantics
- workspace and tool semantics
- session/run/replay truth
- execution-driver and sandbox envelopes
- coordination signals, directives, and reviews
- TypeScript backbone and host-kit surfaces

Everything else in this document depends on that layer staying honest.

### 2. Specialized contract packs

These are narrow contract families that extend BreadBoard into a research/problem domain without redefining kernel truth.

Today the most important ones are:

- ATP contracts for retrieval, specialist solver, decomposition, proof plans, diagnostics, and repair policy
- sandbox envelopes for isolated execution, including heavier or remote execution backends
- DARWIN contract packs for campaign, evidence, claim, and policy artifacts

### 3. Search, optimization, and training overlays

These are not kernel replacements. They are typed subsystems layered over kernel/runtime truth.

- `agentic_coder_prototype.optimize`
  - GEPA-like bounded optimization substrate
  - evaluation/objective/search-space/family structures
  - promotion and transfer/generalization evidence
- `agentic_coder_prototype.search`
  - DAG-shaped search runtime and assessment-bearing search truth
  - paper-fidelity helper layer for RSA, PaCoRe, and related study packets
- `agentic_coder_prototype.rl`
  - graph-native RL overlay
  - replay/live parity, dataset export units, fidelity/conformance manifests, and adapter probe surfaces

### 4. Research-heavy orchestration layers

These are broader experiment and control systems that sit above the lower-level substrates.

- `agentic_coder_prototype.ctrees`
  - conversation-tree and structured reasoning/control research machinery
- `breadboard_ext.darwin`
  - outer-loop evolutionary program layer for campaigns, lanes, evidence, claims, and policy progression

These are the least “productized” of the newer additions, but they are also where a lot of future research leverage lives.

---

## The major systems

## ATP and Lean sandboxes

ATP in BreadBoard is not just “call a prover.” It is a typed contract family for formal reasoning loops.

At its current layer, ATP focuses on:

- retrieval adapter requests and responses
- specialist solver requests, responses, and traces
- decomposition/search loop events and traces
- proof plan IR
- normalized diagnostics
- repair policy decisions
- retrieval snapshots and instrumentation digests

This gives BreadBoard a clean way to represent theorem-proving and formal repair workflows without forcing those semantics into the core kernel.

Lean matters here because it is a canonical example of why sandboxing and ATP have to meet. Formal proving workflows often need strong isolation, reproducible environments, and a way to move between:

- reasoning/control logic
- proof-state or file-state references
- execution and diagnostics
- repair attempts under bounded policies

The sandbox envelopes provide the isolation-side contract. ATP provides the proving-loop-side contract. Together they create a credible substrate for Lean-style formal reasoning workflows and other theorem-proving environments.

## Optimization

Optimization is BreadBoard’s bounded iterative-improvement substrate. It grew through multiple rounds of pressure, but the result is coherent:

- a typed optimization substrate
- dataset and evaluation truth
- objective and benchmark structure
- bounded search spaces and target families
- composed families, transfer slices, and transfer cohorts
- promotion and generalization evidence
- live experiment-cell methodology with Nano/Mini discipline

The core idea is that optimization should stay honest about what is true, what is derived, and what is merely private search/ranking logic.

That doctrine shows up as:

- evaluation remains public truth
- objective breakdown remains derived and inspectable
- reward-like ranking stays backend-private
- transfer and cohort claims are typed and bounded
- promotion remains evidence-gated rather than narrative-driven

Optimization is therefore not an opaque “self-improvement engine.” It is a structured experimentation and bounded-mutation system with strong evidence discipline.

## DAG runtime

The DAG runtime is BreadBoard’s answer to DAG-shaped search and multi-branch reasoning/control programs.

It provides:

- explicit search runs, frontiers, candidates, events, and messages
- barriered schedulers rather than hidden async sprawl
- branch-local state, carry state, and merge/discard semantics
- typed assessments as first-class evaluator/judge/verifier truth
- trajectory export
- paper-fidelity helper artifacts for replication packets and study notes

This makes BreadBoard capable of modeling structures inspired by work like RSA and PaCoRe without baking each paper in as a separate runtime mode.

The crucial architectural win is that the DAG kernel remained small:

- V1 established runtime truth
- V2 added the narrow assessment layer
- V3 added a fidelity/helper layer without expanding runtime nouns

That is exactly the kind of restraint that keeps the system maintainable.

## RL training overlay

The RL layer is not an RL runtime that tries to own the rest of BreadBoard. It is a graph-native overlay that projects RL-relevant truth from runtime and search truth.

It provides:

- rollout, environment, and policy provenance
- trajectory graphs instead of shallow linear episodes
- evaluation annotations and cost ledgers
- credit frames for delayed and shared attribution
- trainer-neutral dataset export units
- replay/live parity guarantees
- export/evaluation manifests and conformance packets
- bounded adapter probe reports

The RL overlay exists because training systems want a cleaner, more stable data plane than raw transcripts or ad hoc logs can provide.

It is deliberately conservative about scope:

- no trainer-specific ontology in kernel truth
- no RL-owned search runtime
- no broad policy-learning orchestration inside BreadBoard
- no claim that every downstream training format should become a first-class kernel concept

## C-Trees

C-Trees are the most research-heavy subsystem in this list.

At a high level, they represent BreadBoard’s tree-shaped reasoning and control research lane: a way to model, score, collapse, rehydrate, and audit structured problem-solving trees with richer closure, baseline, gating, and downstream-task behavior than a plain transcript can carry.

The current module surface suggests several active concerns:

- runtime state and event schemas
- phase machines and policy contracts
- candidate families and baseline contracts
- executor and verifier integration
- branch receipts and closure scoring
- downstream task packs and holdout generalization packs
- historical runner bridges and live benchmark adapters
- practical baseline and control-ablation machinery

The important thing to understand is that C-Trees are not “the same thing as DAG runtime but with another name.”

The DAG runtime is the cleaner, more frozen substrate for search-shaped recipes.

C-Trees are a more experimental structured-reasoning/control program that appears to care about:

- task-tree closure quality
- branching and re-anchoring policy
- helper subtree summarization and rehydration
- verifier/executor relationships
- gating and finish/closure behavior
- stronger live and historical comparison machinery

In other words, DAG is the reusable search substrate; C-Trees are a research lane that may consume several lower-level primitives while investigating richer control structures.

## DARWIN

DARWIN is the outer-loop evolutionary program layer.

It is not the same thing as optimization. It is broader, campaign-oriented, and evidence/program-management-heavy.

The current contract family covers artifacts such as:

- campaign specs
- policy bundles and registries
- lane registries
- candidate artifacts
- evaluation records
- evidence bundles
- claim records
- weekly evidence packets
- effective config and effective policy
- execution plans
- evaluator packs
- decision records
- evolution ledgers

That artifact set tells you what DARWIN is for: not local bounded mutation, but longer-horizon, lane-aware, policy-aware evolution programs with explicit evidence handling and claims.

The boundary between DARWIN and the other systems is one of the most important architectural lines in the repo:

- optimize handles bounded, typed improvement programs
- DAG handles inner-loop search/runtime recipes
- RL handles training-facing data projection and adapter evidence
- DARWIN handles broader outer-loop campaign and evolution management

Keeping those roles separate is one of the biggest reasons the current codebase is still understandable.

---

## How the systems compose

The systems are designed to connect, but not to collapse into each other.

### ATP + sandboxes

ATP uses the sandbox layer to run bounded formal reasoning and repair loops under stable execution contracts. Lean and similar environments are the clearest examples.

### DAG + optimization

DAG produces structured search and assessment truth. Optimization can consume that truth through bounded adapter/export surfaces without importing DAG runtime nouns into the optimization kernel.

### DAG + RL

DAG emits assessment-bearing trajectory truth. RL projects that into graph-native training/export surfaces and then proves replay/live parity and conformance over that projection.

### Optimization + DARWIN

Optimization is the disciplined, bounded inner experimentation lane. DARWIN is the outer evolutionary lane that can reason over candidates, policies, lanes, and claims over longer horizons.

### C-Trees + everything else

C-Trees appear to live in a more experimental position. They likely consume runtime, benchmark, executor, verifier, and baseline machinery from elsewhere while investigating richer task-tree semantics and closure/control behavior.

---

## A practical “when do I use what?” guide

Use ATP when you are dealing with theorem-proving, retrieval-plus-solver loops, proof plans, normalized proof diagnostics, or formal repair policy.

Use sandbox envelopes when you need strong execution isolation, stable execution-driver semantics, or heavyweight backend execution for task slices like Lean, compilation, or controlled tool runs.

Use optimization when the problem is bounded iterative improvement over prompts, overlays, tool guidance, support execution, or related loci, and you need evidence-gated promotion rather than open-ended evolution.

Use DAG runtime when the problem is search-shaped:

- branching
- message passing
- adjudication
- verifier/judge gates
- replayable search trajectories
- paper-faithful replication packets for DAG-style methods

Use RL when the problem is:

- projecting runtime/search truth into training-ready graphs
- export fidelity
- replay/live parity
- trainer-neutral data packaging
- adapter and conformance proof for downstream training systems

Use DARWIN when the problem is:

- campaign orchestration
- policy and lane management
- claim/evidence governance
- longer-horizon evolutionary loops across candidate systems

Use C-Trees when you are operating in the structured tree-control research lane and need richer closure, gating, verifier/executor, or task-pack semantics than the narrower DAG substrate is intended to encode.

---

## Design principles shared across these systems

Across all of these additions, a few principles keep repeating.

### Freeze truth early

The moment a surface becomes semantically important, it needs a typed truth layer. Ad hoc metadata is cheap at first and expensive later.

### Keep helper layers out of the kernel

Several of the newest systems succeeded precisely because they stopped at helper/fidelity/conformance layers instead of widening kernel/runtime truth again.

### Treat evaluation and claims as first-class

This codebase is opinionated about evidence. Diagnostics, evaluations, assessments, and claim records are not afterthoughts.

### Prefer bounded composition over global modes

BreadBoard does not get stronger by adding “one mode for each paper.” It gets stronger by adding narrowly justified primitives that compose across papers and workflows.

### Know where to stop

The optimizer line, DAG line, and RL line all reached points where the correct move was to freeze and use the current substrate under pressure rather than keep expanding nouns.

That discipline is a feature, not a limitation.

---

## Recommended reading paths

If you want the shortest high-quality path into each subsystem:

### ATP and Lean sandboxes

- [ATP and Lean Sandboxing](atp-and-lean-sandboxing.md)
- [ATP Contract Pack](../contracts/atp/README.md)
- [Sandbox Envelopes V1](../contracts/kernel/semantics/sandbox_envelopes_v1.md)

### Optimization

- [Optimization System Overview](optimization-system-overview.md)
- then the V1 → V6 tranche docs linked from the index

### DAG

- [DAG Runtime System Overview](dag-runtime-system-overview.md)
- then the V1 → V3 tranche docs

### RL

- [RL Training System Overview](rl-training-system-overview.md)
- then the RL V1 → V2 tranche docs

### C-Trees

- [C-Trees System Overview](ctrees-system-overview.md)

### DARWIN

- [DARWIN System Overview](darwin-system-overview.md)

---

## Final perspective

The important thing about the merged `main` branch is not that it contains many research subsystems. Plenty of repositories accumulate many subsystems.

What matters is that these subsystems now line up into an understandable architecture:

- kernel truth below
- typed overlays and helper layers above it
- outer-loop orchestration above those
- evidence, replay, and claim discipline throughout

That does not mean the work is finished. Several of these systems remain live research areas. But it does mean BreadBoard has crossed an important threshold: the repo now contains a coherent research platform rather than a pile of isolated experiments.
