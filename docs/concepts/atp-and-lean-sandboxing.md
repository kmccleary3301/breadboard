# ATP and Lean Sandboxing

ATP and sandboxing belong together in BreadBoard.

If you only look at the code from far away, it is easy to misread ATP as “the theorem-proving part” and sandboxing as “the execution backend part.” In practice, the two solve opposite halves of the same problem:

- ATP gives BreadBoard a typed way to express formal reasoning loops, decomposition traces, solver fallbacks, proof plans, diagnostics, and repair decisions.
- sandbox envelopes give BreadBoard a typed way to ask an execution backend to run formal workloads without letting backend details redefine kernel truth.

Lean is the clearest motivating use case for that pairing, but the architectural story is broader: any formal or semi-formal proving stack that needs bounded search, solver specialization, diagnostics, and isolated execution benefits from the same split.

---

## What ATP is in BreadBoard

ATP currently lives as a contract family rather than a giant runtime subsystem.

That choice is deliberate.

BreadBoard needs ATP to be:

- precise enough to capture real retrieval/solver/decomposition/proof-plan workflows
- reusable across multiple theorem-proving or formal-repair environments
- narrow enough that it does not poison the kernel with domain-specific assumptions

The ATP contract pack therefore focuses on a small number of typed artifacts:

- retrieval adapter request/response
- specialist solver request/response
- specialist solver trace
- decomposition node events and loop traces
- proof plan IR
- diagnostic records
- repair policy decisions
- retrieval snapshots
- runtime instrumentation digests

These are not random schemas. Together they define a formal-problem loop that can be inspected and replayed:

1. retrieve the most relevant formal context
2. decompose or structure the proving task
3. call a specialist solver when justified
4. normalize diagnostics into a portable shape
5. make bounded repair-policy decisions
6. record enough evidence to replay or analyze the loop later

That is already a meaningful ATP substrate.

---

## Why ATP is modeled as contracts first

The temptation in theorem-proving infrastructure is to jump straight to a big monolithic proving runtime. BreadBoard resists that.

The contract-first approach buys several things:

### Maintainability

The proving loop is visible in artifacts instead of disappearing into a black-box controller.

### Generalizability

Different proving environments can implement the same contract family without sharing every internal detail.

### Debuggability

Normalized diagnostics and repair-policy decisions make failure analysis much clearer than raw tool output.

### Forward compatibility

If the proving stack grows later, the contracts can remain stable while the internal implementations become more capable.

This is exactly the kind of layering BreadBoard wants across the entire repo.

---

## Why Lean pushes the design

Lean is a useful pressure case because it requires all of the hard parts at once:

- structured formal context
- source and theorem state handling
- compiler/prover execution
- precise diagnostics
- bounded retries and repair policies
- strong environment reproducibility

A good Lean-facing system cannot survive on transcript heuristics alone. It needs:

- typed proving-loop artifacts
- stable state references
- isolated or reproducible execution
- enough instrumentation to separate reasoning failure from environment failure

That is why Lean and sandboxing show up together so naturally in BreadBoard’s architecture.

Lean is not merely “a tool call.” It is a proving environment that exposes why theorem-proving needs both semantic contracts and execution isolation.

---

## The sandbox side of the story

Sandbox envelopes are the execution-side companion to ATP.

The core sandbox semantics define two shared contracts:

- `bb.sandbox_request.v1`
- `bb.sandbox_result.v1`

Those envelopes sit between:

- kernel execution planning
- execution-driver backend implementation
- artifact/evidence collection
- host-visible status and fallback reporting

The important design point is that sandbox envelopes are **backend execution contracts**, not a full standardization of all container or VM fields.

They are meant to represent the kernel-relevant outcome of an execution request:

- what was asked to run
- under what placement, workspace, image, resource, and policy constraints
- what status and evidence came back
- what side effects and artifacts were produced

That is exactly what BreadBoard needs when formal tasks require:

- OCI containers
- stronger isolation layers like gVisor or Kata
- microVM execution
- remote or dedicated execution services

The kernel remains above those details. The envelopes preserve the semantic boundary.

---

## How ATP and sandboxing fit together

A healthy mental model is:

- ATP defines the formal-problem loop
- sandboxing defines the execution envelope for running parts of that loop

For example, a Lean-oriented proving slice may need to:

1. retrieve candidate lemmas or prior proof fragments
2. build a decomposition or proof-plan sketch
3. choose between direct reasoning and a specialist solver
4. compile or check Lean code in an isolated environment
5. normalize errors into portable diagnostics
6. decide whether to patch, retry, fallback, or stop

The ATP artifacts describe the reasoning and repair side.

The sandbox envelopes describe the execution side.

Neither layer should have to fake the other.

That separation is what keeps the architecture legible.

---

## The major ATP primitives

### Retrieval adapter

The retrieval adapter contracts cover the problem of “what context did we ask for, and what did we get back?”

In a formal system, that might include:

- theorem candidates
- tactic hints
- prior proof fragments
- local file/context snapshots
- decomposition-relevant references

The retrieval snapshot contract is especially important because it makes context selection visible. Without that, a proving loop can become impossible to audit.

### Specialist solver

The specialist solver contracts let BreadBoard distinguish between:

- general search/decomposition control
- and a specialized proving or reasoning component with its own request/response/trace shape

This is essential for fallback matrices, controlled delegation, and solver-specific diagnostics.

### Decomposition traces

The search-loop decomposition contracts capture the structure of a proving attempt rather than collapsing everything into one final answer and a pile of logs.

That matters when you need to know:

- what branches were explored
- which assumptions were introduced
- where the proving loop stalled
- how the search geometry changed after repair or retrieval updates

### Proof plan IR

Proof plan IR is the most obviously “formal reasoning” artifact in the ATP set. It exists because proof attempts often need an intermediate representation that is more structured than plain text but less backend-specific than a full prover state.

That IR gives BreadBoard a way to speak about intent and structure without pretending every formal backend shares exactly the same semantics.

### Diagnostics and repair policy

This is one of the strongest parts of the ATP surface.

The repository already includes explicit diagnostics normalization and repair-policy logging documents because theorem-proving workflows fail in many different ways:

- unsupported mode
- incompatible state references
- transport/protocol failures
- proof-check failures
- decomposition mistakes
- missing or stale context

If those failures remain raw strings, the system becomes unmaintainable. Normalized diagnostics let BreadBoard reason about failure classes. Repair-policy decisions then make the next action explicit.

That is exactly the difference between “agent loop with retries” and a research-grade proving substrate.

---

## Lean sandboxes as a use case, not a one-off mode

One of the healthiest choices in the current architecture is that Lean is not being turned into a special global runtime mode.

Instead, Lean is a high-pressure use case that exercises:

- ATP contracts
- sandbox envelopes
- diagnostics normalization
- state references and replay
- repair-policy discipline

That gives BreadBoard flexibility:

- Lean can be a first-class proving target
- without forcing the whole runtime to become “the Lean runtime”

That is the right level of abstraction.

---

## What lives in the API layer today

There is already a bridge surface for ATP-style operations in the CLI bridge layer:

- `/atp/repl`
- `/atp/v1/repl`
- batch variants of those routes

That matters because it means ATP is not only a conceptual contract pack. There is already a real system boundary where ATP-specific interactions can be routed, diagnosed, and normalized.

It also suggests the future shape of the developer experience:

- contract-first schemas and fixtures
- bridge surfaces for ATP-oriented tasks
- bounded runbooks and validators
- proving slices that remain replayable and inspectable

---

## What ATP and Lean sandboxing are not

It helps to say the non-goals plainly.

They are not:

- a single monolithic theorem-proving engine
- an attempt to standardize every prover-state detail
- a backend-specific container or microVM spec
- a promise that all formal methods are equally supported right now
- a justification for leaking prover/runtime quirks into kernel truth

The point is narrower and more durable:

give BreadBoard a clean formal-reasoning and isolated-execution substrate that can support serious proving loops without losing maintainability.

---

## How ATP composes with the rest of BreadBoard

ATP is not an island.

### ATP and optimization

Formal proving and repair loops can become optimization targets when the system needs to improve:

- retrieval policy
- decomposition hints
- prompting overlays
- solver routing
- support envelopes

Optimization should operate over those bounded loci, not replace ATP’s semantic truth.

### ATP and DAG

Some proving workflows may be DAG-shaped:

- verifier loops
- branch-and-check proving paths
- judge/reducer-style synthesis or adjudication

The DAG runtime can model the search geometry, while ATP remains the formal-domain contract pack.

### ATP and RL

RL should not own ATP semantics, but ATP-derived trajectories and evaluations can absolutely become part of RL-facing export packs if the proving workflows become training-relevant.

### ATP and DARWIN

DARWIN should only enter once the proving workflows become outer-loop campaign concerns:

- lane policy
- candidate populations
- claim and evidence packaging
- cross-run evolutionary experimentation

Until then, ATP belongs closer to bounded loop execution than to outer evolutionary governance.

---

## Recommended reading after this page

If you want the precise source docs behind this overview:

- [ATP Contract Pack](../contracts/atp/README.md)
- [ATP Diagnostics Normalization V1](../contracts/atp/ATP_DIAGNOSTICS_NORMALIZATION_V1.md)
- [ATP Repair Policy Logging V1](../contracts/atp/ATP_REPAIR_POLICY_LOGGING_V1.md)
- [ATP Repair Operator Library V1](../contracts/atp/ATP_REPAIR_OPERATOR_LIBRARY_V1.md)
- [Sandbox Envelopes V1](../contracts/kernel/semantics/sandbox_envelopes_v1.md)

---

## Final perspective

ATP and Lean sandboxing are important because they demonstrate a design habit BreadBoard is now using repeatedly:

- formalize the semantic loop
- formalize the execution boundary
- keep both typed and inspectable
- refuse to hide important failure or repair logic in “smart controller” folklore

That is why these additions matter beyond formal proving itself. They are a model for how BreadBoard wants to grow: carefully, with contracts first, and with enough structure that difficult domains remain readable.
