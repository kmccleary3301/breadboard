# DAG Runtime System Overview

BreadBoard’s DAG runtime is the repository’s opt-in search and adjudication substrate for DAG-shaped workflows.

It exists because some important reasoning and control patterns do not fit cleanly into:

- a plain agent transcript
- a single long-running loop
- or a pure optimizer mutation/evaluation cycle

Recent research patterns often involve:

- branching candidate sets
- barriered rounds
- message passing
- verifier or judge gates
- explicit pruning and selection
- structured trajectory export

The DAG runtime is BreadBoard’s answer to that class of problem.

It is not a giant scheduler framework. It is a deliberately bounded runtime that tried very hard not to become one.

---

## The central idea

The DAG runtime treats search as explicit typed truth.

Instead of hiding the search geometry in controller logic, it gives BreadBoard a first-class representation of:

- the run
- the frontier
- the candidates
- the branches
- the messages
- the carry state
- the assessments
- the events and resulting trajectory

That makes search inspectable, replayable, exportable, and easier to compose with downstream systems.

---

## Why BreadBoard needed this subsystem

BreadBoard’s broader ambitions include replication and composition of approaches inspired by work such as:

- recursive self-aggregation
- parallel reasoning/adjudication
- verifier-guided branch search
- reducer-style synthesis and filtering

Those patterns are not well-served by:

- a flat list of tool calls
- transcript-only reasoning traces
- or search policies that exist only in local experiment code

The DAG runtime gives these patterns a home without pretending every paper deserves its own runtime mode.

---

## The core runtime truth

At the center of the system are the runtime nouns:

- `SearchRun`
- `SearchFrontier`
- `SearchCandidate`
- `SearchEvent`
- `SearchMessage`
- `SearchCarryState`
- `SearchBranchState`
- `SearchWorkspaceSnapshot`

These are the objects that describe what happened in a search-shaped program.

They answer questions like:

- what was in the frontier
- which candidates existed at a point in time
- what branches or local state they carried
- what messages were passed
- what workspace state mattered
- which operations changed the frontier or selected candidates

This is the part of the subsystem that should remain small and stable.

---

## The barriered-scheduler philosophy

One of the best choices in the DAG subsystem is what it refused to do.

It did **not** start by becoming an async orchestration jungle.

Instead, the runtime centers on deterministic barriered schedulers:

- a barriered round scheduler
- bounded message-passing schedulers
- assessment gates that operate at clear selection boundaries

That matters for several reasons:

- it keeps execution easier to reason about
- it keeps replay and parity tractable
- it keeps the search geometry visible
- it reduces accidental scheduler-specific semantics

In other words, the runtime favors explicit rounds and gates over hidden concurrency tricks.

---

## The assessment layer

The narrow but decisive second step in the subsystem was the assessment layer.

`SearchAssessment` exists because search systems frequently need grounded judgment truth:

- verifier outputs
- judge or reducer outputs
- execution or proof results
- pairwise ranking verdicts

Before V2, it was possible to model parts of this with event metadata and candidate statuses. After V2, those judgments became first-class.

That was the right move because it unlocked cleaner modeling across multiple recipe families:

- frontier verify
- judge reduce
- branch execute verify

The assessment layer is one of those rare additions that immediately made several different patterns simpler at once. That is usually a sign the abstraction is real.

---

## The runtime is not just for one paper

The DAG runtime is often easiest to explain through paper-inspired examples, but it should not be mistaken for a paper registry.

Its purpose is to support a class of structures:

- parallel candidate generation
- barriered synthesis
- adjudication and filtering
- multi-round branch programs
- verifier-guided or judge-guided narrowing
- structured search trajectories for export and analysis

BreadBoard used real paper pressure to justify the runtime, but the end result is a reusable substrate rather than a “mode per paper” library.

---

## V1: search truth surface

The first DAG round established:

- the search truth surface
- a deterministic barriered scheduler
- RSA-style reference examples
- typed compaction and carry-over
- bounded message passing
- branch-local state and merge/discard semantics
- trajectory export and bounded reward hooks

That was the point where the subsystem became a real runtime rather than a set of notes.

---

## V2: assessment-bearing search

The second round refused broad kernel growth and instead added one missing shape:

- `SearchAssessment`

That led to:

- assessment registries
- event and trajectory linkage
- barriered assessment gates
- widened coverage of verifier/judge/execute-verify families

This is the round that made the runtime feel grounded rather than merely structural.

It also proved something important: the missing pressure was about judgment truth, not “more scheduler” or “more state.”

---

## V3: fidelity and experiment operations

The third DAG round did not change the kernel at all.

Instead, it added a helper layer above the frozen runtime:

- `PaperRecipeManifest`
- `FidelityScorecard`
- `ComputeBudgetLedger`
- `BaselineComparisonPacket`
- `ReplicationDeviationLedger`

That helper layer exists because once the runtime was strong enough, the next bottleneck became:

- paper-faithful replication
- compute normalization
- fidelity labeling
- baseline comparison
- cross-paper synthesis

This was the right kind of growth: helper artifacts, not more runtime nouns.

---

## The fidelity helper layer

The V3 helper layer is easy to underestimate, but it is one of the subsystem’s most mature choices.

It says:

- runtime truth should stay frozen
- paper fidelity and experiment-packaging concerns should sit above it

That gives BreadBoard a way to do serious paper-oriented work without polluting the runtime with:

- paper-specific modes
- experiment-manager ontology
- replication-specific hacks

The helper layer is therefore not “extra paperwork.” It is the abstraction that keeps paper-faithful work from destabilizing the runtime.

---

## What the subsystem can now express

With V1 through V3 complete, BreadBoard’s DAG system can express:

- branching frontier search
- verifier-guided filtering
- judge/reducer-style adjudication
- bounded message-passing rounds
- branch-local carry state and merge/discard behavior
- assessment-linked trajectories
- offline search dataset export
- RSA-style and PaCoRe-style replication packets
- budget-matched baseline packets
- cross-paper synthesis packets

That is a meaningful level of capability.

It does not mean the long-term DAG research space is finished. It does mean the current kernel is doing its job.

---

## How DAG relates to the rest of BreadBoard

## DAG and optimization

The DAG runtime can emit structured search and assessment truth that optimization can consume through bounded adapter surfaces.

Optimization does not need to absorb DAG runtime semantics to benefit from DAG evidence.

## DAG and RL

The RL overlay projects trajectory and assessment truth from the DAG/runtime layer into graph-native RL-friendly exports.

DAG stays responsible for search/runtime truth.

RL stays responsible for training-facing projection, manifests, conformance, and adapter proof.

## DAG and DARWIN

The DAG runtime is inner-loop search/runtime structure.

DARWIN is outer-loop campaign and policy evolution.

Once a workflow becomes:

- campaign-like
- lane-managed
- archive- or evidence-program-oriented
- claim/governance-heavy

it is likely moving into DARWIN territory rather than asking the DAG runtime to become larger.

## DAG and C-Trees

DAG is the cleaner, more frozen reusable runtime for search-shaped patterns.

C-Trees are the more experimental tree-control research lane.

They may overlap in inspiration, but they should not be treated as the same subsystem.

---

## What the DAG runtime is not

It is not:

- a global async orchestration framework
- a paper-mode registry
- a public search-policy ontology
- a replacement for optimization
- a training framework
- a campaign system

This list matters because the subsystem remained good precisely by saying “no” to those temptations.

---

## Typical use cases

Use DAG runtime when you need:

- an explicit frontier of candidates
- branching and barriered selection
- judge/verifier/adjudicator truth as first-class search input
- message passing between bounded rounds
- branch-local state and merge/discard semantics
- trajectory export for offline study or RL projection
- paper-faithful replication packets for DAG-shaped approaches

Do not start with DAG if the problem is mainly:

- bounded local prompt/config mutation
- broader candidate campaign governance
- trainer-specific export and conformance

Those belong elsewhere.

---

## Recommended reading after this overview

For the public subsystem arc:

- [DAG Runtime V1 search surface](dag-runtime-v1-search-surface.md)
- [DAG Runtime V2 assessment surface](dag-runtime-v2-assessment-surface.md)
- [DAG Runtime V2 E4 widening packet](dag-runtime-v2-e4-widening-packet.md)
- [DAG Runtime V2 optimize adapter note](dag-runtime-v2-optimize-adapter-note.md)
- [DAG Runtime V2 DARWIN boundary](dag-runtime-v2-darwin-boundary.md)
- [DAG Runtime V2 RL-facing note](dag-runtime-v2-rl-facing-note.md)
- [DAG Runtime V3 fidelity helper layer](dag-runtime-v3-fidelity-helper-layer.md)
- [DAG Runtime V3 RSA replication packet](dag-runtime-v3-rsa-replication-packet.md)
- [DAG Runtime V3 PaCoRe replication packet](dag-runtime-v3-pacore-replication-packet.md)
- [DAG Runtime V3 cross-paper composition](dag-runtime-v3-cross-paper-composition.md)

If you need maintainer-only freeze and decision records, start from
[../internals/research/README.md](../internals/research/README.md) instead of
treating those notes as part of the public reading path.

---

## Final perspective

The most important thing BreadBoard’s DAG work accomplished was not “support for DAGs.” That phrase is too vague to be useful.

What it actually achieved was:

- explicit search truth
- explicit judgment truth
- a refusal to overgrow the runtime
- a helper layer for fidelity and replication instead of more kernel sprawl

That is what makes the subsystem credible. It is not only expressive. It is interpretable.
