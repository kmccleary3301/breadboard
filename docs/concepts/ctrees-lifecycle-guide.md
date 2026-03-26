# C-Trees Lifecycle Guide

This guide is the practical companion to the C-Trees overview.

The overview explains what C-Trees are for. This document explains how to read the package as a lifecycle:

- what a tree is made of
- how runtime phases move
- what branch receipts do
- how finish and closure are enforced
- how helper rehydration and downstream evaluation fit in

The current C-Trees package is still a research lane rather than a single frozen product surface, so this guide is necessarily interpretive. But it is grounded in the actual current module family on `main`.

---

## Start from the schema

The most stable way to understand C-Trees is to begin with the schema layer.

At the core are:

- `CTreeEvent`
- `CTreeNode`

and a small set of shared vocabularies:

- node types:
  - `objective`
  - `task`
  - `artifact`
  - `evidence_bundle`
  - `checkpoint`
  - `message`
- node statuses:
  - `planned`
  - `active`
  - `blocked`
  - `done`
  - `frozen`
  - `archived`
  - `superseded`
  - `abandoned`
- graph link types:
  - `blocks`
  - `waits_for`
  - `conditional_blocks`
  - `validates`
  - `caused_by`
  - `relates_to`
  - `duplicates`
  - `supersedes`
  - `replies_to`

That tells you something important immediately:

C-Trees are not only storing a branching search. They are storing a typed, auditable work graph with:

- objectives
- tasks
- artifacts
- evidence
- checkpoints
- messages

This is much closer to a structured reasoning-and-work tree than to a plain frontier search.

---

## The likely lifecycle in plain language

A practical way to read the package is as a lifecycle with five concerns:

1. define the tree and its nodes
2. move local runtime work through phases
3. require explicit branch receipts for key decisions
4. enforce finish and closure conditions
5. evaluate, summarize, rehydrate, or bridge the resulting tree

Several modules in the package line up cleanly with that reading.

---

## Phase progression

The current phase machine is intentionally small:

- `localize`
- `commit_edit`
- `edit`
- `verify`
- `close`

And it enforces a legal next-step graph:

- `localize -> commit_edit | verify`
- `commit_edit -> edit | verify`
- `edit -> verify | commit_edit`
- `verify -> close | commit_edit | localize`
- `close -> []`

This small transition graph is revealing.

It suggests that the current runtime story is built around a repair-style or bounded task-completion loop:

- localize the issue
- commit or perform the necessary edit
- verify the change
- either loop back or close

That is much more concrete than a vague “tree search.”

It means the current C-Trees lane is not merely exploring arbitrary branching. It is trying to govern task completion with explicit structure.

---

## Branch receipts

The branch-receipt contract is one of the clearest windows into what C-Trees care about.

When enabled, it defines:

- the executor family and variant
- the support strategy
- the phase order
- tool allowlists by phase
- budgets for localize, branch, commit, proof, and verify turns
- finish rules
- turn rules
- prompt-level behavioral requirements

The important idea is simple:

branch choice should be explicit and visible to the runner, not merely implied by shell output or a final summary.

This is a strong design choice. It means C-Trees want the system to show:

- what branch it took
- what evidence supports that branch
- what tools were allowed during that phase
- whether the branch can be accepted as valid

That is much stricter than “the agent happened to do some steps and then said it was done.”

### Why branch receipts matter

Branch receipts appear to be the answer to a subtle but important question:

how do you keep a structured work tree honest when multiple plausible resolutions or subpaths exist?

The answer in C-Trees is:

- make the branch decision visible
- constrain the tools allowed in that phase
- require receipt-bearing evidence for proof or verification stages

This is one of the most distinctive parts of the subsystem.

---

## Finish and closure

The finish-closure contract builds on the branch-receipt contract rather than replacing it.

That is an excellent signal: closure is not treated as a separate decorative phase. It is downstream of verified structured work.

The finish-closure contract adds requirements such as:

- explicit finish receipt requests
- denial of generic mark-complete behavior
- closure-ready gating
- no-tool-turn restrictions around closure

The prompt block is also revealing: receipt visibility alone is not enough. The task must be closure-ready, and finish must line up with the active branch and visible basis.

That means the subsystem distinguishes between:

- “we have some evidence”
- and “the tree is actually ready to close”

That distinction is exactly the kind of thing mature tree-control systems need, and many lack.

---

## Tool discipline inside the lifecycle

Both branch-receipt and finish-closure contracts specify phase-level tool allowlists.

That matters because it turns the tree lifecycle into an executable policy:

- localize can inspect
- branch-lock can record decisions
- commit/edit can apply changes
- proof and verification phases can gather the right evidence
- close can only happen through explicit closure mechanisms

That is a significant increase in interpretability.

Instead of a tree merely being “the thing that happened,” the tree has:

- phase semantics
- permitted action surfaces
- explicit completion requirements

That is exactly what makes a structured control program readable after the fact.

---

## Runtime state, hooks, and audits

The wider package suggests that the lifecycle above is surrounded by:

- runtime state tracking
- executor and verifier-executor hooks
- branch receipt hooks
- finish closure hooks
- runtime and branch/closure audit slices

This indicates a system trying to do two things simultaneously:

- govern the live control loop
- and later audit whether it actually followed the intended contract

That combination is one of the strongest signs that C-Trees are not just an experimental data structure. They are a serious attempt at structured control governance.

---

## Helper summary and rehydration

Several C-Trees modules focus on:

- helper subtree summaries
- coupling canaries
- helper rehydration

These modules suggest that C-Trees are also addressing an important practical problem:

how do you keep a rich tree useful over time without carrying every internal detail forward at full resolution?

The likely answer is:

- summarize helper subtrees
- preserve the right coupling information
- allow bounded rehydration when the details become relevant again

This is a major research concern for long structured workflows. It is also one of the places where C-Trees differ most from plain DAG runtime, which is narrower and more direct about branch/search truth.

---

## Baselines, holdouts, and downstream tasks

The presence of:

- baseline contracts
- historical fixtures
- holdout generalization packs
- downstream task packs
- downstream task eval

means C-Trees are not only concerned with internal structure. They are also trying to answer:

- is this tree policy actually better?
- how does it behave on downstream tasks?
- does it generalize?
- can we compare it to historical and practical baselines?

This is why C-Trees should be understood as a research lane, not merely an engine feature.

They are tying structure to evaluation.

---

## A practical reading path through the package

If you want to understand the package directly from code, this is the most sensible order.

### 1. Tree shape

Start with:

- `schema.py`
- `events.py`
- `runtime_state.py`

This tells you what the tree stores.

### 2. Control loop

Then read:

- `phase_machine.py`
- `policy.py`
- `runtime_policy_contract.py`
- `control_contract.py`

This tells you how the live process is supposed to move.

### 3. Branch and finish governance

Then read:

- `branch_receipt_contract.py`
- `branch_receipt_state.py`
- `branch_receipt_hooks.py`
- `finish_closure_contract.py`
- `finish_closure_state.py`
- `finish_closure_hooks.py`

This tells you how the system prevents ambiguous branch selection and premature closure.

### 4. Evidence and execution coupling

Then read:

- `executor_contract.py`
- `verifier_executor_contract.py`
- related hooks and state modules

This tells you how action and evidence interact.

### 5. Evaluation and bridge layers

Finally read:

- baseline modules
- historical bridge modules
- live benchmark adapter modules
- downstream task and holdout modules

This tells you how the tree is judged.

---

## The most important design insight

If you only take one thing from this guide, it should be this:

C-Trees are trying to make branch choice and closure explicit, enforceable, and auditable.

That is what separates them from many vague “tree of thoughts” style systems.

The tree is not only a metaphor for reasoning. It is a governed lifecycle with:

- phase transitions
- budgeted stages
- tool restrictions
- branch receipts
- closure conditions

That is a much stronger and more useful design stance.

---

## Final perspective

The C-Trees package still needs more polished top-level documentation than the newer DAG and RL systems now have. But the underlying shape is already visible.

It is a structured-control research lane concerned with:

- explicit branch choice
- evidence-bearing receipts
- finish and closure rigor
- helper summary and rehydration
- baseline and generalization pressure

That is enough to make the subsystem legible, and it gives future readers a better starting point than browsing dozens of modules without a map.
