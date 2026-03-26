# DARWIN Campaign Workflow Guide

The DARWIN overview explains what the outer-loop evolutionary layer is for. This guide explains how to work with it cleanly.

The key practical challenge in DARWIN is not understanding one artifact. It is keeping the whole campaign stack coherent:

- campaign specs
- lanes
- policy bundles
- candidate artifacts
- evaluations
- evidence bundles
- claims
- weekly packets
- decisions
- evolution ledgers

When these artifacts line up, DARWIN is one of the clearest large-scale research program layers in the repo. When they do not, it can quickly become difficult to reason about what the program is actually doing.

This guide is about keeping them lined up.

---

## Start with the right question

Do not reach for DARWIN first just because the work is ambitious.

DARWIN is the right layer only when the question is genuinely outer-loop, for example:

- how should a research program be structured into lanes?
- what policy bundle governs a lane or tranche?
- how should evidence and claims be packaged over time?
- what decision history explains why the program moved?

If the question is still only:

- which bounded candidate should advance?
- how should one search recipe behave?
- how should one dataset export path be validated?

then DARWIN is probably too high in the stack.

---

## The canonical DARWIN workflow

## 1. Define the campaign

Start with the campaign spec.

This is where the program says:

- what the campaign is about
- what success and failure look like
- what families or lanes exist
- what evidence categories matter

If the campaign spec is vague, everything downstream becomes harder to interpret.

## 2. Register the lanes

The lane registry is how the campaign becomes structured instead of monolithic.

Use lanes to separate materially different tracks such as:

- research baseline lanes
- systems or scheduling lanes
- family-aware proving lanes
- challenge or challenge-refresh lanes

Lanes are not merely labels. They create accountability around policy and evidence.

## 3. Bind policy explicitly

Once lanes exist, bind the relevant policy bundles and registries.

This is where you say:

- what routing or transfer rules apply
- what promotion or claim rules apply
- what evaluator pack or budget discipline applies

The policy layer should be explicit enough that future readers can answer:

- what rules governed this tranche?
- what changed when the program changed direction?

That is why effective policy artifacts exist.

## 4. Emit candidate and evaluation artifacts

Candidate artifacts and evaluation records are where the outer loop meets actual work.

At this stage, DARWIN should not replace lower-level truth. It should package and govern it.

That means:

- candidate artifacts should point to real bounded work
- evaluation records should remain grounded
- evaluator packs should be explicit when they matter

DARWIN is strongest when it sits above strong inner-loop evidence rather than inventing new truth at the top.

## 5. Bundle evidence and ladder claims carefully

Evidence bundles and claim records are where many research programs become sloppy.

BreadBoard’s DARWIN layer is explicitly built to avoid that.

Use evidence bundles to gather:

- what happened
- what was measured
- what comparisons were made
- what readiness gates were passed or failed

Use claim records to state:

- what the program believes it can say
- at what evidence tier
- with what boundaries

Claims should never be stronger than the evidence bundle makes possible.

## 6. Record decisions and update the evolution ledger

This is the difference between a campaign and a pile of slices.

If the program changes:

- lane emphasis
- transfer policy
- policy bundle
- challenge status
- evidence interpretation

that should be visible in decision records and later visible again in the evolution ledger.

Without that, long-horizon research programs become very hard to reconstruct honestly.

---

## The most important artifacts in practice

## Campaign spec

This is the outer-loop anchor. It should be stable enough that later slices feel like a continuation of a known program, not a replacement for one.

## Lane registry

This keeps the program multi-track without becoming chaotic.

## Policy bundle and policy registry

These keep routing, evaluator, transfer, and claim behavior explicit.

## Candidate artifact and evaluation record

These are the bridge to the lower-level systems that actually generated evidence.

## Evidence bundle and weekly evidence packet

These are the narrative and reporting backbone of the campaign.

## Claim record

This is where BreadBoard’s evidence discipline stays visible at the outer loop.

## Decision record and evolution ledger

These preserve strategic memory.

---

## A practical lane pattern

A clean multi-lane DARWIN program usually looks like:

### One campaign

With a single campaign spec and explicit boundaries.

### Several lanes

Each lane has:

- a purpose
- a policy posture
- evaluation expectations
- promotion or claim boundaries

### Shared evidence discipline

Even when the lanes differ, the evidence and claim packaging should remain mutually intelligible.

### Explicit decisions about escalation

When a lane is widened, retired, or reframed, capture that in decision records rather than relying on status notes alone.

That pattern is what makes DARWIN scalable without becoming opaque.

---

## How DARWIN should compose with the rest of BreadBoard

## With optimization

DARWIN should consume optimization evidence, promotion outputs, and package or cohort-level outcomes.

It should not replace bounded optimization logic.

## With DAG

DARWIN can consume DAG-derived comparison packets or replication outputs, but DAG should remain the inner-loop runtime for search-shaped methods.

## With RL

DARWIN may later consume RL-facing evidence or training-related findings, but RL should remain the owner of training-facing truth and conformance.

## With ATP

DARWIN can package and govern proving-oriented evidence at the program level, but ATP should remain the owner of formal loop semantics.

This is the recurring rule:

DARWIN packages, governs, and evolves. It should not absorb the inner-loop truth models.

---

## Common campaign mistakes

## Mistake 1: using DARWIN to hide weak inner-loop evidence

If the underlying optimize/DAG/RL/ATP evidence is weak, no amount of campaign packaging will fix it.

## Mistake 2: making lane names stand in for policy

Lanes need policies and registries. Otherwise the lane structure becomes decorative.

## Mistake 3: overclaiming from weekly packets

Weekly packets summarize. They do not replace grounded candidate, evaluation, and evidence artifacts.

## Mistake 4: failing to record strategic decisions

This makes the program impossible to reconstruct honestly later.

---

## A good reading order for new DARWIN work

Use this path:

1. [DARWIN System Overview](darwin-system-overview.md)
2. [docs/contracts/darwin/README.md](../contracts/darwin/README.md)
3. [DARWIN Contract Pack V0](../contracts/darwin/DARWIN_CONTRACT_PACK_V0.md)
4. [DARWIN Architecture Boundary V0](../contracts/darwin/DARWIN_ARCHITECTURE_BOUNDARY_V0.md)
5. [DARWIN Lane Registry V0](../contracts/darwin/DARWIN_LANE_REGISTRY_V0.md)
6. [DARWIN Policy Registry V0](../contracts/darwin/DARWIN_POLICY_REGISTRY_V0.md)
7. then the stage/tranche docs relevant to the campaign you are working on

---

## Final perspective

DARWIN is the place where BreadBoard’s large research programs become governable.

Its job is not to be flashy. Its job is to make longer-horizon work:

- structured
- policy-explicit
- claim-disciplined
- evidence-backed
- reconstructable over time

That is a bigger contribution than it may first appear. In large research systems, outer-loop clarity is often the first thing to disappear. DARWIN exists so it does not.
