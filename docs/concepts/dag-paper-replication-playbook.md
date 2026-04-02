# DAG Paper Replication Playbook

BreadBoard’s DAG runtime is now mature enough that the main challenge is no longer “can it represent DAG-shaped search at all?” The main challenge is fidelity:

- can it replicate the structure of a target paper honestly?
- can it do so without inventing paper-specific runtime modes?
- can it compare the result under explicit compute and baseline discipline?

This playbook explains how to do that cleanly.

---

## The replication philosophy

BreadBoard’s current DAG line reached the right conclusion:

- the runtime kernel should stay small
- paper-faithful work should mostly happen in helper/fidelity layers

That means a good replication effort should avoid two bad extremes.

### Bad extreme 1: “we support the paper” with no real fidelity accounting

This usually means the method has been loosely approximated but not honestly tracked.

### Bad extreme 2: paper-specific runtime growth

This usually means the runtime starts accumulating one-off nouns or modes for each new paper.

The V3 helper layer exists to avoid both.

---

## The core replication packet

The key helper artifacts for paper-faithful DAG work are:

- `PaperRecipeManifest`
- `FidelityScorecard`
- `ComputeBudgetLedger`
- `BaselineComparisonPacket`
- `ReplicationDeviationLedger`

A serious replication effort should treat those as the default packet, not as optional paperwork.

### PaperRecipeManifest

This is where you say what structure you believe you are modeling.

Use it to capture things like:

- number of rounds
- fan-out geometry
- compaction strategy
- message-passing topology
- sequential versus parallel structure
- gating rules

### FidelityScorecard

This is where you say how close the modeled recipe is to the intended one.

This keeps “paper-like” claims honest.

### ComputeBudgetLedger

This is where you stop vague comparisons from sneaking in.

If the compared baselines are not compute-normalized or at least budget-explicit, the replication packet is weaker than it looks.

### BaselineComparisonPacket

This is where the replication becomes comparative rather than merely descriptive.

### ReplicationDeviationLedger

This is where you say what drifted and why.

This is one of the most valuable artifacts because it prevents silent reinterpretation of the paper.

---

## A recommended replication workflow

## 1. Start by identifying the paper pressure correctly

Ask:

- is the pressure about search/runtime structure?
- or about evaluation/training/outer-loop governance instead?

If the missing pressure is not really DAG-local, do not force it into the DAG runtime.

## 2. Model the recipe with existing runtime truth first

Before adding any helper artifacts, make sure the existing runtime can already express:

- the frontier structure
- branches
- message passing if needed
- assessments if needed
- trajectory export

If the runtime itself cannot express the method honestly, that is a kernel-pressure question.

If it can, stay out of the kernel.

## 3. Add the paper helper packet

Now define:

- the recipe manifest
- the fidelity scorecard
- the compute budget ledger
- the baseline packet
- the deviation ledger

This is the point where the replication becomes a real study rather than a suggestive example.

## 4. Define the baseline matrix

A good replication packet should almost always compare the modeled recipe to at least one of:

- flattened baseline
- transcript-only or conclusion-only baseline
- no-message or no-assessment ablation
- sequential baseline for a parallel method
- budget-matched local baseline

This matters because many paper-inspired systems only look strong until the right baseline is made explicit.

## 5. Record deviations honestly

Not every divergence is fatal. The problem is hidden divergence.

Use the deviation ledger to distinguish:

- deliberate simplification
- runtime constraint-driven drift
- evaluation-side substitution
- compute-side normalization difference

That gives future readers a fair account of what was and was not replicated.

---

## Replicating RSA cleanly

RSA is a good first replication family because it is structurally simpler and easier to budget.

The clean RSA packet should answer:

- what are `N`, `K`, and `T` in this slice?
- how are rounds or recursion levels bounded?
- what is the budget-matched baseline?
- what is the sequential or simpler comparison?
- what fidelity claims are being made versus merely implied?

BreadBoard’s V3 RSA tranche already established the right kind of packet:

- fixed-seed `N / K / T` helpers
- budget-matched baselines
- synthesized replication packet

The practical lesson is:

start with RSA when you want a cleaner fidelity discipline before taking on a more complicated message-passing paper.

---

## Replicating PaCoRe cleanly

PaCoRe-like work is where replication discipline gets harder.

The main risks are:

- hand-waving over round geometry
- vague compaction choices
- implicit parallel-versus-sequential drift
- message passing that is “sort of there” but not structurally faithful

The right packet should make at least these comparisons explicit:

- exact round profile
- parallel versus sequential
- message-passing ablations
- compaction baseline
- conclusion-only compaction baseline where relevant

If those comparisons are missing, the replication is likely overclaiming.

---

## When a replication should push back on the kernel

Most replication work should stay above the runtime kernel now. But not all pressure belongs there.

A replication may justify future kernel work only if:

- two or more materially different target families expose the same missing runtime truth shape
- the missing shape cannot be represented cleanly with current assessments, branches, messages, or helper layers
- the missing shape is clearly runtime-local rather than evaluator- or export-local

If the pressure is about:

- fidelity scoring
- compute normalization
- baseline reporting
- export slices

that is almost certainly helper-layer work, not kernel work.

---

## How DAG replication should compose with optimization

This is the clean path when you want to reuse DAG replication work downstream.

Use DAG replication packets to produce:

- structured comparison evidence
- bounded export slices
- fidelity-labeled results

Then let optimization consume those packets when needed through:

- comparison packets
- benchmark inputs
- promotion-adjacent evidence

Do not turn the DAG runtime itself into a benchmark or promotion engine.

---

## How DAG replication should compose with RL

If the replication effort is likely to feed training-facing work later, the right move is:

- keep the runtime and fidelity packet in DAG
- define bounded RL-facing export slices
- let RL own the projection, parity, and conformance layers

That is already the architecture BreadBoard settled on in DAG V3 and RL V1/V2, and it is the correct one to preserve.

---

## Common replication mistakes

## Mistake 1: collapsing fidelity into a prose claim

If the fidelity is real, it should survive as a scorecard and deviation ledger.

## Mistake 2: comparing unnormalized compute paths

This is one of the fastest ways to produce impressive but misleading replication results.

## Mistake 3: hiding the weak baseline

If a graph-native method only looks good because the baseline is underspecified, the packet is not finished.

## Mistake 4: widening the kernel for one paper

A good runtime should support many papers through a few reusable primitives. If every new paper adds new runtime nouns, the system is drifting.

---

## Recommended reading path

Use this sequence:

1. [DAG Runtime System Overview](dag-runtime-system-overview.md)
2. [DAG Runtime V3 fidelity helper layer](dag-runtime-v3-fidelity-helper-layer.md)
3. [DAG Runtime V3 RSA replication packet](dag-runtime-v3-rsa-replication-packet.md)
4. [DAG Runtime V3 PaCoRe replication packet](dag-runtime-v3-pacore-replication-packet.md)
5. [DAG Runtime V3 cross-paper composition](dag-runtime-v3-cross-paper-composition.md)
6. [Research Systems Walkthroughs](research-systems-walkthroughs.md)

---

## Final perspective

BreadBoard is now in a good position for DAG paper replication because it resisted the wrong kind of growth.

The runtime can stay stable while the fidelity/helper layer does the work of:

- recipe specification
- baseline discipline
- compute normalization
- deviation tracking

That is exactly how a serious replication program should look in a maintainable repository.
