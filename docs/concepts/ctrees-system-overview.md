# C-Trees System Overview

C-Trees are BreadBoard’s most research-heavy structured reasoning and control lane.

They are easy to misread because they do not present themselves as a single polished runtime package with a small, frozen contract surface. Instead, they currently appear as a wide research module family concerned with:

- tree-shaped reasoning and control
- branch receipts and closure behavior
- executor and verifier integration
- helper-summary and rehydration behavior
- candidate families and baselines
- runtime state, gating, and policy
- historical and live benchmark bridging

That makes C-Trees both important and a little unusual.

This overview explains what they seem to be doing, how they differ from DAG runtime, and how to think about them inside the merged BreadBoard architecture.

---

## The central idea

If DAG runtime is BreadBoard’s reusable substrate for search-shaped recipes, C-Trees appear to be BreadBoard’s richer research lane for tree-shaped control and closure.

The names in the current module family strongly suggest that C-Trees are exploring questions such as:

- how should tree branches be created, anchored, and re-anchored?
- what counts as a valid branch receipt?
- how do verifier and executor states interact across branches?
- when is a tree ready to finish, collapse, or rehydrate?
- how should helper subtrees be summarized and later reconstructed?
- how should candidate baselines, holdouts, and downstream task packs be evaluated?

That is a very different center of gravity from transcript handling or even plain frontier search.

---

## Why C-Trees matter

BreadBoard’s broader ambition is not only to run agents. It is to model and improve complex reasoning/control programs in a way that is auditable and reusable.

Some of those programs are not adequately described by:

- one transcript
- one branch frontier
- or one optimizer target

They involve deeper notions of:

- closure
- decomposition quality
- helper reuse
- baseline alignment
- control policy over tree structure
- branch receipts and finish conditions

That is the territory C-Trees seem to occupy.

---

## What the current module surface tells us

Even without a single consolidated C-Trees concept doc yet, the package itself is revealing.

The module family currently includes surfaces for:

- schema and events
- runtime state and policy contracts
- executor, verifier-executor, and hooks
- branch receipt contracts, hooks, scoring, and state
- finish closure contracts, hooks, state, and scoring
- helper subtree summary, coupling, and rehydration
- live grounding and live benchmark adapters
- candidate families and baselines
- holdout generalization and downstream task evaluation
- phase machines and audit slices
- persistence, store, compiler, runner, and watchdog machinery

That is enough to infer a few important things.

### 1. C-Trees are not just a data structure

They are a control system research lane.

The presence of policy, hooks, phase machines, watchdogs, and closure contracts shows that the tree is not just being stored. It is being governed.

### 2. C-Trees care a lot about “finish”

The number of modules around closure, completion, and finish behavior suggests that ending a tree correctly is a first-class problem.

That is an unusually important signal. Many experimental tree systems are good at branching and bad at ending. BreadBoard is explicitly treating finish behavior as an architectural concern.

### 3. C-Trees are deeply concerned with verifier and executor interaction

The verifier-executor modules and branch-receipt surfaces imply a system that is trying to keep action, evidence, and closure tightly coupled.

### 4. C-Trees are benchmark-aware

Historical bridges, live benchmark adapters, and baseline modules indicate that this is not only a reasoning toy. It is being pushed against real comparative and evaluation pressure.

---

## How C-Trees differ from DAG runtime

This boundary is worth making explicit because the two systems can look similar from far away.

## DAG runtime

DAG is the cleaner, reusable, more frozen substrate for:

- frontier search
- barriered rounds
- message passing
- assessment-bearing adjudication
- search trajectory export
- paper-fidelity packets for DAG-shaped methods

## C-Trees

C-Trees appear to be the richer experimental lane for:

- tree-shaped control programs
- stronger closure and rehydration semantics
- helper subtree management
- baseline and candidate family studies
- branch receipts and finish policies
- deeper executor/verifier coupling

So the simplest accurate distinction is:

- DAG is the narrower reusable search runtime
- C-Trees are the broader structured-tree control research program

That does not mean C-Trees are “better” or “later.” It means they are operating in a different part of the design space.

---

## The likely architectural role of C-Trees

C-Trees seem to live between runtime semantics and outer evaluation programs.

They likely consume or depend on:

- kernel task/subagent semantics
- workspace and execution truth
- verifier/executor results
- benchmark adapters
- baselines and holdout evaluation

They may also eventually inform:

- improved task-tree control strategies
- better decomposition or closure policies
- higher-level research claims about structured agentic reasoning systems

This makes them less like a single product subsystem and more like a research laboratory embedded in the codebase.

---

## Why C-Trees should stay conceptually separate from DARWIN

It is tempting to see a broad research/control system and assume it should merge into DARWIN.

That would be a mistake.

DARWIN is the outer evolutionary campaign layer:

- lanes
- policies
- evidence bundles
- claims
- decision records

C-Trees are closer to the inner control/reasoning/program-structure problem.

Even if DARWIN later evaluates or evolves tree-based strategies, C-Trees should remain conceptually about the structure of reasoning/control trees themselves, not the governance of long-horizon campaign evolution.

---

## Why C-Trees should stay conceptually separate from RL

C-Trees may eventually produce trajectories or evaluative slices that are useful to RL-facing exports.

But RL’s job is narrower:

- graph-native trajectory projection
- replay/live parity
- conformance
- dataset and adapter evidence

C-Trees are about how the structured tree itself is built, governed, closed, and compared.

That is a different concern.

---

## Typical use cases for C-Trees

Reach for C-Trees when the problem is about:

- tree-shaped reasoning or decomposition
- branch receipt and closure logic
- helper subtree summarization and rehydration
- executor/verifier coupled task trees
- baseline-heavy structured reasoning evaluation
- holdout and downstream-task generalization studies on tree-structured programs

Do not start with C-Trees if what you really need is:

- a simple bounded search runtime
- trainer-neutral dataset export
- bounded package mutation and promotion
- campaign orchestration and claims governance

Those already have better homes.

---

## The maturity story

C-Trees appear less frozen than optimization, DAG, or RL.

That is not necessarily a weakness. It likely reflects where the research pressure still is.

The right expectation is:

- C-Trees are active and substantial
- C-Trees are not yet as narratively consolidated as the other recent subsystems
- C-Trees likely remain a place where significant conceptual work is still happening

That is why a good overview matters. Without one, readers can see the breadth of the package and miss the shape of the research program.

---

## How to approach the C-Trees package

If you are trying to understand the package practically, a good path is:

1. start with the schema, events, runtime state, and phase machine modules
2. inspect branch receipt and finish closure contracts
3. look at candidate/baseline/generalization modules
4. then inspect live benchmark adapters, historical bridges, and audit slices

That sequence mirrors the likely conceptual structure:

- what the tree is
- how it evolves
- how it closes
- how it is evaluated

---

## What still needs documentation

Compared with the other subsystems, C-Trees still need deeper narrative documentation for:

- the canonical end-to-end lifecycle
- the major phase-machine states and transitions
- the relationship between branch receipts, closure scoring, and rehydration
- the candidate family story
- the historical/live evaluation bridge

This document is the entrypoint, not the final word.

---

## Final perspective

C-Trees matter because they represent BreadBoard’s willingness to investigate a harder class of structured reasoning/control systems than a transcript or a frontier search can express cleanly.

They are currently the least “polished” of the newer major subsystems in documentation terms, but also one of the most promising in research terms.

The right way to read them is:

- not as unfinished DAG
- not as hidden DARWIN
- and not as RL infrastructure

but as BreadBoard’s tree-structured reasoning and closure research lane, sitting beside the more stabilized search, optimization, and training overlays.
