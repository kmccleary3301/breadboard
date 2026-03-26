# Research Systems Quickstart

This is the shortest practical path into BreadBoard’s newer research-facing subsystems.

Use this page if you already understand the repository at a high level and want to answer one of these questions quickly:

- Which subsystem should I reach for first?
- What is the fastest good reading path for ATP, DAG, RL, optimization, C-Trees, or DARWIN?
- Where are the current operator-facing docs rather than the historical tranche notes?

If you are new to the repo entirely, start with [FIRST_RUN_5_MIN.md](FIRST_RUN_5_MIN.md) and [../INDEX.md](../INDEX.md) first.

---

## The one-paragraph map

BreadBoard now has several serious research layers above the kernel:

- ATP plus sandboxing for formal reasoning and isolated proving/execution workflows
- optimization for bounded GEPA-like improvement programs with typed evidence and promotion
- DAG runtime for search-shaped branching, adjudication, and paper-faithful replication work
- RL overlays for graph-native trajectory export, replay/live parity, conformance, and adapter probes
- C-Trees for richer tree-structured reasoning/control research
- DARWIN for outer-loop campaign, lane, policy, evidence, and claim governance

The systems are meant to compose. They are not meant to collapse into one giant framework.

---

## Choose your path

### If your problem is formal reasoning, theorem proving, or Lean-style execution

Start with:

1. [../concepts/atp-and-lean-sandboxing.md](../concepts/atp-and-lean-sandboxing.md)
2. [../concepts/atp-formal-workflows.md](../concepts/atp-formal-workflows.md)
3. [../contracts/atp/README.md](../contracts/atp/README.md)
4. [../contracts/kernel/semantics/sandbox_envelopes_v1.md](../contracts/kernel/semantics/sandbox_envelopes_v1.md)

Use ATP when the hard part is:

- retrieval and decomposition
- solver fallback
- proof diagnostics
- bounded repair logic

Use sandboxing when the hard part is:

- isolated execution
- backend placement
- reproducible heavy proving environments

### If your problem is bounded candidate improvement or package-scoped optimization

Start with:

1. [../concepts/optimization-system-overview.md](../concepts/optimization-system-overview.md)
2. [../concepts/optimization-study-playbook.md](../concepts/optimization-study-playbook.md)
3. then the optimization tranche docs in `docs/concepts/`

Use optimization when the hard part is:

- mutation boundaries
- evaluation and benchmark structure
- family/package/cohort claims
- promotion and generalization evidence

### If your problem is branching, adjudication, or paper-inspired search structure

Start with:

1. [../concepts/dag-runtime-system-overview.md](../concepts/dag-runtime-system-overview.md)
2. [../concepts/dag-paper-replication-playbook.md](../concepts/dag-paper-replication-playbook.md)
3. then the DAG V1–V3 docs in `docs/concepts/`

Use DAG when the hard part is:

- search geometry
- verifier/judge truth
- branch-local state
- message passing
- paper-faithful replication packets

### If your problem is training-facing export, replay/live parity, or trainer adapter proof

Start with:

1. [../concepts/rl-training-system-overview.md](../concepts/rl-training-system-overview.md)
2. [../concepts/rl-adapterization-playbook.md](../concepts/rl-adapterization-playbook.md)
3. then the RL V1–V2 docs in `docs/concepts/`

Use RL when the hard part is:

- graph-native trajectory truth
- export fidelity
- conformance
- downstream adapter support claims

### If your problem is structured tree control, closure, or helper rehydration

Start with:

1. [../concepts/ctrees-system-overview.md](../concepts/ctrees-system-overview.md)
2. [../concepts/ctrees-lifecycle-guide.md](../concepts/ctrees-lifecycle-guide.md)

Use C-Trees when the hard part is:

- tree lifecycle
- branch receipt logic
- finish closure
- helper subtree summary or rehydration
- tree-specific baseline or downstream-task evaluation

### If your problem is campaign governance, lanes, policy bundles, or long-horizon evidence programs

Start with:

1. [../concepts/darwin-system-overview.md](../concepts/darwin-system-overview.md)
2. [../concepts/darwin-campaign-workflow-guide.md](../concepts/darwin-campaign-workflow-guide.md)
3. [../contracts/darwin/README.md](../contracts/darwin/README.md)

Use DARWIN when the hard part is:

- campaign structure
- lane management
- policy governance
- evidence and claim packaging
- decision and evolution tracking

---

## If you are not sure where the problem belongs

Read these three docs in order:

1. [../concepts/research-systems-overview.md](../concepts/research-systems-overview.md)
2. [../concepts/research-systems-composition-playbook.md](../concepts/research-systems-composition-playbook.md)
3. [../concepts/research-systems-walkthroughs.md](../concepts/research-systems-walkthroughs.md)

Those three documents together answer:

- what each subsystem is for
- how to keep the boundaries clean
- what actual repo entrypoints to use

---

## The fastest honest subsystem checklist

Use this when a new idea arrives and you need to classify it quickly.

### Reach for ATP first if

The core pressure is theorem-proving or formal-repair semantics.

### Reach for optimization first if

The core pressure is bounded improvement, comparison, and promotion.

### Reach for DAG first if

The core pressure is branch/search/adjudication structure.

### Reach for RL first if

The core pressure is training-facing graph projection or export fidelity.

### Reach for C-Trees first if

The core pressure is structured tree control and closure.

### Reach for DARWIN first if

The core pressure is outer-loop campaign and evidence governance.

---

## A final rule that saves time

If a subsystem already solves the problem at the right layer, compose with it.

Do not create a new layer just because the work feels ambitious.

BreadBoard’s strongest recent progress came from:

- freezing good truth surfaces
- adding narrow helper layers when pressure justified them
- and keeping outer-loop concerns out of inner-loop runtimes

This quickstart is really a shortcut to that design habit.
