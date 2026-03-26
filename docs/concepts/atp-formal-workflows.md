# ATP Formal Workflows

This document turns the ATP contract pack into a practical workflow map.

The ATP overview explains why BreadBoard models theorem-proving and formal repair as typed contracts. This page explains how those contracts fit into the actual proving workflows that exist in the repository today, especially around:

- retrieval and decomposition loops
- specialist solver fallback
- normalized diagnostic handling
- Hilbert-oriented formal evaluation slices
- sandbox-backed execution for heavier proving environments such as Lean

It is also intentionally honest about current support.

BreadBoard has a real ATP program on `main`, including Hilbert runner and contract surfaces. It also has a broader older ATP/Lean branch history that should be treated as selective-salvage context, not as if all of that broader scaffolding were already cleanly productized on current `main`.

---

## What a formal workflow needs

A serious ATP workflow usually needs five different things at once:

1. a way to select or retrieve useful formal context
2. a way to decompose the problem or choose a solving path
3. a way to execute proving or checking steps in a controlled environment
4. a way to normalize failure and support repair logic
5. a way to replay, compare, and audit the resulting behavior

BreadBoard now has typed surfaces for each of those concerns.

That is the main reason ATP in this repository feels more substantial than “prompt the model and hope.”

---

## The canonical workflow shape

The best high-level mental model for current ATP work is:

1. capture the formal problem and any state references
2. retrieve bounded relevant context
3. build a decomposition or proof-plan sketch
4. optionally route to a specialist solver
5. run the proving or checking step in a sandbox-capable execution environment when needed
6. normalize the diagnostics
7. choose a repair or fallback action
8. record enough evidence for replay, comparison, and tranche-level analysis

The ATP schemas are the typed representation of that shape.

---

## Retrieval and decomposition

Two of the most important ATP contracts are the retrieval adapter and decomposition traces.

### Retrieval

Retrieval is where the system answers:

- what context was requested
- how much was requested
- what came back
- what snapshot of supporting evidence or references existed at that moment

In theorem-proving settings, this may include:

- theorem candidates
- prior proofs
- tactic or pattern hints
- local file-state references

The key benefit is auditability. The retrieval snapshot makes context selection visible instead of leaving it as hidden prompt scaffolding.

### Decomposition

Formal problems often become tractable only after they are decomposed.

That decomposition might represent:

- subgoals
- proof-plan branches
- staging of solver handoff
- repair-path search

BreadBoard’s decomposition trace artifacts let that process be recorded as a real structure rather than a purely textual “thinking trace.”

That distinction matters in practice. When a proving loop fails, it is often important to know whether:

- the retrieved context was bad
- the decomposition geometry was bad
- the solver path was wrong
- or the backend execution failed for environmental reasons

Typed retrieval and decomposition artifacts make that diagnosis much easier.

---

## Specialist solver fallback

Not every formal task should be handled by the same reasoning path.

That is why ATP includes specialist solver request/response/trace surfaces.

These let BreadBoard model a workflow where:

- a general controller sets up the task
- a specialist solver is invoked for a bounded subproblem
- the result and trace come back in a typed way
- fallback logic remains explicit

This is especially important for:

- solver routing experiments
- fallback matrices
- proving slices that need domain-specialized behavior without turning the entire runtime into that specialist

In other words, ATP can acknowledge specialization without becoming a pile of special cases.

---

## Diagnostics and repair

This is one of the most practically valuable parts of the ATP work.

Formal workflows fail in several distinct ways:

- unsupported mode
- missing or incompatible state references
- protocol and transport failures
- invalid decomposition
- actual proof or compilation failure
- stale or insufficient context

BreadBoard already has explicit diagnostics normalization work for ATP. That gives the system a common language for failure.

Once diagnostics are normalized, repair-policy decisions become a real layer:

- retry
- fallback
- request more context
- route to a specialist
- patch and rerun
- stop and record the failure cleanly

Without these two layers, formal work tends to devolve into unstructured retries.

With them, the repository can support serious analysis of proving behavior.

---

## Hilbert as the current concrete proving program

The ATP program on current `main` includes a meaningful Hilbert-facing slice:

- runner scripts
- comparison-pack builders
- scoreboards and rollups
- repair-intensity and canonical-baseline tooling
- ATP REPL and adapter slices

This matters because it anchors ATP in actual work rather than only schemas.

The Hilbert-oriented scripts indicate a real proving/evaluation workflow around:

- formal pack execution
- canonical baseline comparison
- invalid extract tracking
- result conversion and rollup
- scoreboard generation
- repair and replay analysis

That gives BreadBoard a genuine ATP proving program to document and extend.

---

## Lean as the heavier proving pressure case

Lean should be understood as the use case that makes sandboxing and ATP meet.

The current repo state supports the underlying pieces:

- ATP contracts and diagnostics
- sandbox envelopes
- Firecracker-oriented ATP scripts
- state-ref and adapter slices

But it is still important to be precise:

- the current merged `main` contains the substrate and ATP/Hilbert program cleanly
- broader historical ATP/Lean scaffolding exists in older diverged branch history
- that older branch history should inform future work, but should not be mistaken for a fully integrated current-product surface

That is actually a healthy documentation stance. It tells readers both:

- there is a real Lean-relevant path here
- and the current support story is substrate-first, not “fully finished Lean product”

---

## Where sandboxing enters the workflow

Not every ATP slice needs heavy sandboxing. But once the formal environment requires:

- isolated file/state handling
- dedicated prover or compiler execution
- stronger execution trust boundaries
- reproducible environment packaging

the sandbox envelopes become important.

In those cases, the workflow becomes:

1. ATP decides what proving/checking step should run
2. the sandbox layer carries that step into an execution backend
3. the result comes back as a sandbox result plus ATP-relevant diagnostics and evidence

That is exactly the right split.

The ATP layer remains semantically rich.

The sandbox layer remains execution-rich.

Neither one becomes a dumping ground for the other’s concerns.

---

## Current useful entrypoints in the repo

For people trying to work with ATP today, the following surfaces matter most.

### Contract and schema docs

- [ATP Contract Pack](../contracts/atp/README.md)
- [ATP Diagnostics Normalization V1](../contracts/atp/ATP_DIAGNOSTICS_NORMALIZATION_V1.md)
- [ATP Repair Policy Logging V1](../contracts/atp/ATP_REPAIR_POLICY_LOGGING_V1.md)
- [ATP Repair Operator Library V1](../contracts/atp/ATP_REPAIR_OPERATOR_LIBRARY_V1.md)

### Bridge/API surfaces

- [agentic_coder_prototype/api/cli_bridge/atp_router.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/agentic_coder_prototype/api/cli_bridge/atp_router.py)
- [agentic_coder_prototype/api/cli_bridge/atp_diagnostics.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/agentic_coder_prototype/api/cli_bridge/atp_diagnostics.py)

### ATP/Hilbert scripts

- [scripts/run_bb_formal_pack_v1.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/run_bb_formal_pack_v1.py)
- [scripts/run_bb_atp_adapter_slice_v1.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/run_bb_atp_adapter_slice_v1.py)
- [scripts/build_atp_hilbert_scoreboard_v1.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/build_atp_hilbert_scoreboard_v1.py)
- [scripts/build_atp_hilbert_rollup_v1.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/build_atp_hilbert_rollup_v1.py)
- [scripts/build_atp_hilbert_canonical_baselines_v1.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/build_atp_hilbert_canonical_baselines_v1.py)
- [scripts/atp_firecracker_ci.sh](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_integration_main_20260326/scripts/atp_firecracker_ci.sh)

These are the best concrete starting points if you want to see how the ATP substrate is actually being exercised.

---

## Recommended workflow patterns

## Pattern 1: contract-first slice

Use this when introducing a new formal backend or proving slice.

Start by deciding:

- what retrieval payloads need to be typed
- what solver handoffs need their own requests/responses
- what diagnostics must be normalized
- what repair decisions must become explicit

Only then should you widen runners or scripts.

## Pattern 2: sandbox-backed proving slice

Use this when the proving environment is heavyweight, stateful, or isolation-sensitive.

The ATP layer should define the reasoning loop.

The sandbox layer should carry the execution envelope.

Do not blur those together.

## Pattern 3: comparator and rollup lane

Use this when the main question is not “can it prove?” but “how does this proving behavior compare across systems, seeds, or repair intensities?”

That is where the current Hilbert rollup and scoreboard scripts are especially relevant.

---

## Common mistakes to avoid

## Mistake 1: treating diagnostics as plain logs

If the failure is important, it should be normalized.

## Mistake 2: letting sandbox details redefine ATP meaning

The proving loop should remain intelligible even if the backend execution layer changes.

## Mistake 3: overclaiming Lean support

The correct current claim is that BreadBoard has a real ATP substrate, real Hilbert-facing proving infrastructure, and real sandbox-aligned execution contracts that make Lean-style support credible.

That is stronger and more honest than pretending the entire broader Lean story is already fully productized on current `main`.

---

## Final perspective

ATP formal workflows matter because they show BreadBoard doing something difficult correctly:

- formal reasoning is kept typed
- execution isolation is kept separate
- diagnostics and repair are explicit
- proving results are comparable and replayable

That is already a meaningful formal-systems substrate.

The right next documentation work after this page would be even more concrete runbooks around:

- the Hilbert runner pipeline
- ATP bridge request flows
- sandbox-backed proving execution
- and future selective integration of broader Lean-oriented scaffolding where it becomes justified.
