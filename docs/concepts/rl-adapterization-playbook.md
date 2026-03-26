# RL Adapterization Playbook

The RL overlay is now mature enough that the next practical challenge is not “what is a trajectory graph?” It is:

How do we turn graph-native runtime truth into useful, honest, trainer-facing adapter paths without allowing adapter needs to distort BreadBoard’s truth surfaces?

That is what this playbook covers.

---

## What “adapterization” means here

Adapterization in BreadBoard’s RL lane is not a synonym for “integrate a trainer.”

It means:

- defining export bundles and manifests that a downstream system can consume
- proving replay/live parity for those exports
- measuring what information is preserved or lost
- recording workload-family-specific support and friction
- keeping trainer-specific concerns out of BreadBoard truth surfaces unless repeated pressure forces a shared helper layer

This is why RL V2 added:

- `EvaluationPackManifest`
- `ExportManifest`
- `AdapterProbeReport`

These are the minimal helper surfaces needed to make adapter work honest.

---

## The adapter ladder

BreadBoard documentation should treat adapters as belonging to one of three support levels.

### Experimental

The path exists, but support claims are still weak.

You may have:

- an export unit shape
- a local packing or conversion script
- partial conformance

You do not yet have enough evidence to describe the path as stable.

### Probe-backed

This is the important middle stage.

A probe-backed adapter has:

- explicit workload family
- an `AdapterProbeReport`
- fidelity and conformance evidence
- explicit unsupported fields or lossy transforms

This is the right default support language for most new adapters.

### Supported

This level should be used sparingly.

To call an adapter genuinely supported, BreadBoard should have:

- stable manifests
- replay/live parity on representative workloads
- explicit conformance packet coverage
- low-friction or well-characterized transformation behavior

Until that evidence exists, “probe-backed” is the healthier claim.

---

## The basic adapter workflow

## 1. Start from the right export unit

Choose the export shape that actually matches the downstream need:

- `sft_distillation`
- `rl_transition_segment`
- `verifier_example`

If none of those are appropriate, first ask whether the missing need is:

- only adapter-local packaging
- or repeated enough to justify a new helper export surface

Do not invent a new export type casually.

## 2. Generate or inspect the trajectory graph

Everything downstream depends on the graph projection being honest.

Before adapter work, verify:

- the graph is complete enough for the workload family
- the causal edges and evaluation annotations are present
- the needed compaction and credit information survives projection

If the graph itself is inadequate, that is an RL overlay question, not an adapter packaging question.

## 3. Build the export manifest

The export manifest should make visible:

- source workload family
- export kind
- provenance and versioning
- split policy
- contamination controls
- replay/live origin
- canonicalization or transform version

This is the artifact that stops dataset-building from becoming folklore.

## 4. Build the evaluation pack manifest when evaluator coherence matters

If the downstream adapter depends on evaluator or verifier annotations in a meaningful way, use the evaluation-pack manifest to keep that bundle coherent and explicit.

This is especially important when:

- verifier examples are exported
- delayed evaluations are involved
- workload quality depends on the evaluator pack, not just the raw trajectory

## 5. Run conformance and parity

Before claiming usefulness, check:

- replay/live export parity
- canonical export fingerprints
- conformance packet results
- transform and loss reporting

If the adapter path only works live and cannot be replay-audited, it is not ready to be described as strong.

## 6. Produce an adapter probe report

The probe report is where you record:

- what workload family was tested
- what fields were preserved
- what fields were unsupported
- what transforms or flattenings occurred
- what friction points were encountered

This is one of the most important habits in RL V2.

---

## What a good adapter probe report should answer

Every good probe report should answer:

- what workload family was exercised?
- which export kind was used?
- was the export replay/live parity-stable?
- what information was lost or flattened?
- what fields were unsupported?
- what manual wrapper or tensorization steps were still required?
- what fidelity tier should this path honestly claim?

If a report does not answer those, it is not yet doing the job.

---

## Compaction fidelity and delayed evaluation

These are the two places where adapter paths often fail silently.

## Compaction fidelity

A downstream consumer may accept the export bundle while still losing the information that made the trajectory useful.

That is why RL V2 explicitly added compaction fidelity reports and export fingerprints.

The right question is not only “did the adapter parse it?” but:

- did the export preserve enough semantic resolution to remain useful?

## Delayed evaluation

Many realistic agentic workloads do not produce all evaluation signals immediately.

If a downstream path cannot preserve or align delayed evaluation correctly, the dataset may still look well-formed while becoming semantically wrong.

That is why delayed-evaluation fidelity is a first-class concern.

---

## Weak baselines matter here too

One of the healthiest pieces of the RL V2 work is that it included weak baselines in the pressure-study packet:

- transcript-only
- flattened
- scalar-reward-only

Adapterization should keep using those baselines.

Why?

Because they reveal whether the graph-native export path is genuinely better or only more complicated.

If a downstream system performs just as well with a flattened or transcript-only baseline on the relevant workload family, that is important evidence.

It does not necessarily invalidate the richer export. It may simply mean the richer export is not yet needed for that workload.

Either way, the evidence matters.

---

## What should stay out of BreadBoard

This is the most important restraint in adapter work.

BreadBoard should not absorb:

- trainer-specific optimization state
- packing/sharding implementation details
- framework-specific tensor layouts
- broad training-orchestration control

Those are downstream concerns.

BreadBoard’s job is to provide:

- truthful graph-native export surfaces
- fidelity and conformance evidence
- probe-backed support language

That is enough.

---

## When a new shared helper surface may be justified

Most adapter pressure should stay adapter-local.

A new helper surface is justified only if:

- the same missing structure appears across multiple adapter families
- the problem is not specific to one trainer or one packing path
- the missing structure affects fidelity or support claims materially

This is why `EvaluationPackManifest`, `ExportManifest`, and `AdapterProbeReport` were good additions:

they solved repeated pressure without becoming a trainer framework.

---

## Recommended reading path

Use this sequence:

1. [RL Training System Overview](rl-training-system-overview.md)
2. [RL V1 alpha exporters](rl-training-primitives-v1-alpha-exporters.md)
3. [RL V2 fidelity hardening](rl-training-primitives-v2-fidelity-hardening.md)
4. [RL V2 export conformance](rl-training-primitives-v2-export-conformance.md)
5. [RL V2 adapter probes](rl-training-primitives-v2-adapter-probes.md)
6. [RL V2 pressure-study packet](rl-training-primitives-v2-pressure-study-packet.md)
7. [RL V2 freeze and deferrals](rl-training-primitives-v2-freeze-and-deferrals.md)

---

## Final perspective

The right way to think about RL adapterization in BreadBoard is:

- not “how do we become a trainer framework?”
- but “how do we make faithful, replay-stable, graph-native exports easy to trust and honest to consume?”

The subsystem is now strong enough to answer that question well, as long as adapter work remains disciplined.
