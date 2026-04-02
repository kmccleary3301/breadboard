# RL Training System Overview

BreadBoard’s RL subsystem is an overlay for training-facing truth, export, fidelity, and conformance.

That sentence is deliberately specific.

It is **not**:

- a replacement runtime
- a trainer framework
- a search engine
- or a generic “RL mode”

The RL work in BreadBoard began from a simpler observation: if the repository is going to support serious downstream training workflows, the data plane cannot be a pile of transcripts and ad hoc reward annotations.

The system needed a graph-native, replayable, trainer-neutral overlay that could project from runtime truth and then prove that the resulting datasets were faithful.

That is what the current RL stack does.

---

## The central idea

BreadBoard separates four kinds of truth:

- runtime truth
- policy-view truth
- evaluation truth
- export truth

The RL overlay sits on top of the first three and gives the fourth a stable shape.

This is a much stronger design than simply saying “our transcript is the trajectory.”

Why?

Because many BreadBoard workloads are:

- multi-agent
- branchy
- partially asynchronous
- evaluator-rich
- compaction-sensitive
- replay-relevant

A shallow linear episode format cannot carry that honestly.

---

## Why the RL subsystem is an overlay

The overlay design is one of the subsystem’s best architectural choices.

The kernel and DAG/search layers already know about:

- runs
- branches
- assessments
- events
- workspaces
- messages
- continuation and checkpoint semantics

The RL layer should not try to own those semantics. It should project from them.

That makes the RL subsystem:

- more maintainable
- more composable with search and optimization
- less likely to duplicate runtime truths
- easier to adapt for multiple downstream training systems

In other words, it earns its place by being a clean consumer and translator of truth, not by trying to become the new center of the repo.

---

## The core RL contract pack

The first RL round established the trainer-neutral contract pack:

- `RolloutDescriptor`
- `EnvironmentDescriptor`
- `PolicyProvenance`
- `TrajectoryGraph`
- `EvaluationAnnotation`
- `CostLedger`
- `CreditFrame`
- `DatasetExportUnit`
- `CompactionManifest`
- `AdapterCapabilities`
- optional `TrainingFeedback`

This is already a substantial surface, but it is coherent.

Each piece answers a different question.

## Rollout and environment descriptors

These make the “what was run?” and “under what environment conditions?” story explicit.

That is essential for:

- reproducibility
- trainer dataset provenance
- cross-adapter comparisons
- contamination and split discipline

## Policy provenance

This keeps policy identity and origin visible rather than letting it blur into generic dataset metadata.

For training systems, this matters because “where did this behavior come from?” is often as important as the behavior itself.

## Trajectory graph

This is the heart of the subsystem.

Instead of flattening everything into a list of steps, BreadBoard builds a graph with:

- tracks
- observations
- decisions
- effects
- causal edges

That lets the export layer preserve:

- branches
- join/discard structure
- message visibility
- delayed evaluator effects
- workspace-related causality

This is the major improvement over the earlier shallow trajectory assumptions that existed elsewhere in the repo.

## Evaluation annotation, cost ledger, and credit frame

These three are what make the overlay serious rather than cosmetic.

They preserve:

- what was judged and by whom
- what costs and usage were incurred
- how attribution should work in delayed or shared-result settings

Without those, downstream training datasets quickly become ambiguous.

## Dataset export unit and compaction manifest

These provide a trainer-neutral export surface while also preserving enough metadata to understand:

- what got compacted
- what kind of unit is being exported
- how the export was derived

This is how BreadBoard avoids tying itself too early to one trainer or one data-packing format.

---

## V1: build the graph-native substrate

The first RL round established the overlay properly.

It did several important things:

- created the RL contract pack
- made trajectory projection graph-native
- proved live-versus-replay projection parity
- shipped alpha exporters for:
  - SFT/distillation
  - RL transition segments
  - verifier examples
- hardened multi-agent and async causality
- ran bounded adapter probes
- froze the surface rather than expanding it immediately

This made RL in BreadBoard real, not speculative.

It also clarified a key point: the old shallow linear trajectory surfaces were no longer the right place to anchor future RL-facing work.

---

## V2: fidelity, conformance, and adapter pressure

The second RL round was the right kind of follow-up.

Instead of growing the core ontology again, it focused on proof problems:

- replay/live export fidelity
- compaction fidelity
- delayed-evaluation fidelity
- canonical export fingerprinting
- export conformance packets
- adapter probe evidence
- pressure-study comparisons against weak baselines

This round introduced a few helper/conformance-layer artifacts:

- `EvaluationPackManifest`
- `ExportManifest`
- `AdapterProbeReport`

These are justified because they solve repeated export/provenance/conformance pressure without changing the overlay’s semantic center.

That is exactly how a mature subsystem should grow.

---

## Why replay matters so much

One of the strongest judgments in the RL work is that replay-derived export is not just a debug trick. It is an audit path.

This matters because if a dataset can only be trusted when built live, then:

- parity becomes hard to prove
- conformance becomes brittle
- contamination controls get weaker
- downstream confidence drops

By forcing replay/live parity into the subsystem early, BreadBoard made the RL data plane more trustworthy.

That is a major architectural win.

---

## The conformance and probe layer

RL V2 made another mature move: it stopped pretending that adapter support should be declared by vibes.

Instead, it added:

- conformance packets
- fidelity tiers
- explicit split and contamination reporting
- bounded probe reports for serving, evaluator, dataset, and trainer-feedback paths

This lets BreadBoard make narrower, more honest claims:

- what a given adapter path preserves
- what is unsupported
- what losses or flattenings happen
- which workload families are covered

That is far better than building a broad support matrix with weak semantics.

---

## Typical export/use cases

The RL subsystem is now appropriate when you need:

- graph-native trajectory extraction from runtime/search truth
- replay/live parity for training data generation
- trainer-neutral export units
- evaluator-rich or verifier-rich data slices
- cost- and credit-aware training corpora
- proof that a downstream adapter is faithful enough for a bounded workload family

It is also the natural place to ask hard questions like:

- did compaction destroy training-relevant information?
- can delayed evaluations still be attributed correctly?
- can replay reproduce the same export bundle?
- is the trainer-facing projection honest about what it lost?

---

## What the RL subsystem is not

It is not:

- a custom RL algorithm library
- a runtime-owned search framework
- an excuse to duplicate DAG runtime nouns
- a public trainer-state contract pack
- a generic benchmark or rubric manager

Those non-goals are important because the subsystem’s strength comes from staying narrow.

---

## How RL composes with the rest of BreadBoard

## RL and DAG

The relationship with DAG is especially important.

DAG owns:

- search geometry
- search assessments
- frontier truth
- branch/search runtime semantics

RL owns:

- projection of those truths into training-facing graphs and export units
- replay/live fidelity
- conformance and adapter evidence

That boundary is healthy and should stay that way.

## RL and optimization

Optimization can consume RL-facing evidence or packets when useful, but RL is not the optimizer.

Optimization remains about bounded improvement programs. RL remains about graph-native training/export truth and proof of dataset usefulness.

## RL and DARWIN

DARWIN is where outer-loop campaign and policy evolution live.

RL may eventually provide better data or evidence to those systems, but it should not absorb campaign governance or broad evolutionary orchestration.

---

## Recommended reading after this overview

For the public subsystem arc:

- [RL V1 boundary and contract pack](rl-training-primitives-v1-boundary-and-contract-pack.md)
- [RL V1 replay parity](rl-training-primitives-v1-replay-parity.md)
- [RL V1 alpha exporters](rl-training-primitives-v1-alpha-exporters.md)
- [RL V1 multi-agent async hardening](rl-training-primitives-v1-multi-agent-async-hardening.md)
- [RL V2 fidelity hardening](rl-training-primitives-v2-fidelity-hardening.md)
- [RL V2 export conformance](rl-training-primitives-v2-export-conformance.md)
- [RL V2 adapter probes](rl-training-primitives-v2-adapter-probes.md)
- [RL V2 pressure-study packet](rl-training-primitives-v2-pressure-study-packet.md)

If you need maintainer-only closeout and deferral notes, start from
[../internals/research/README.md](../internals/research/README.md) instead of
using those records as part of the public subsystem arc.

---

## Final perspective

BreadBoard’s RL subsystem is important not because it claims to solve RL. It is important because it solves the part that BreadBoard genuinely needed:

- faithful graph-native trajectory truth
- stable export and provenance surfaces
- replay/live auditability
- bounded conformance and adapter evidence

That is the difference between “we could probably export something for training” and “we have a credible training-facing substrate.”
