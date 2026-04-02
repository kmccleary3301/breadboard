# Research Systems Walkthroughs

This document is the commands-and-files companion to the research systems overview and playbooks.

It is intentionally practical. It does not try to cover every option. It gives you a small set of real entrypoints in the current merged repository so you can move from “I understand the concept” to “I can navigate the code and artifacts.”

Use this together with:

- [Research Systems Overview](research-systems-overview.md)
- [Research Systems Composition Playbook](research-systems-composition-playbook.md)

---

## Ground rule

These walkthroughs are not claiming that every workflow here is one command away from a finished product experience.

They are showing:

- the current maintained doc entrypoints
- the most relevant code packages
- the canonical scripts or tests that exercise the subsystem

That is the right level of honesty for the current repo state.

---

## Walkthrough 1: ATP and formal workflows

### Read first

- [ATP and Lean Sandboxing](atp-and-lean-sandboxing.md)
- [ATP Formal Workflows](atp-formal-workflows.md)
- [ATP Contract Pack](../contracts/atp/README.md)

### Inspect the main ATP surfaces

```bash
sed -n '1,220p' docs/contracts/atp/README.md
sed -n '1,220p' agentic_coder_prototype/api/cli_bridge/atp_router.py
sed -n '1,220p' agentic_coder_prototype/api/cli_bridge/atp_diagnostics.py
```

### Inspect the current ATP runners

```bash
sed -n '1,220p' scripts/run_bb_atp_adapter_slice_v1.py
sed -n '1,220p' scripts/run_bb_formal_pack_v1.py
sed -n '1,220p' scripts/build_atp_hilbert_scoreboard_v1.py
```

### What this tells you

- the contract pack is real
- the CLI bridge has ATP-specific routing and diagnostics normalization
- the current proving lane is manifest-driven and Hilbert-oriented
- ATP on `main` is not just theory; it has real proving/evaluation tooling

---

## Walkthrough 2: optimization

### Read first

- [Optimization System Overview](optimization-system-overview.md)
- [Optimization Study Playbook](optimization-study-playbook.md)

### Inspect the public optimization surface

```bash
sed -n '1,260p' agentic_coder_prototype/optimize/__init__.py
```

### Inspect the current main test surfaces

```bash
ls tests/test_optimize_*.py
```

### Run the focused optimization test slice

```bash
PYTHONPATH=. pytest -q tests/test_optimize_substrate.py \
  tests/test_optimize_dataset.py \
  tests/test_optimize_evaluation.py \
  tests/test_optimize_backend.py \
  tests/test_optimize_suites.py \
  tests/test_optimize_promotion.py
```

### What this tells you

- the optimization surface is now large but coherent
- there is a clear public module export surface
- the subsystem is exercised through explicit substrate, benchmark, suite, backend, and promotion tests rather than one huge opaque harness

---

## Walkthrough 3: DAG runtime and paper replication

### Read first

- [DAG Runtime System Overview](dag-runtime-system-overview.md)
- [DAG Paper Replication Playbook](dag-paper-replication-playbook.md)

### Inspect the current DAG export surface

```bash
sed -n '1,260p' agentic_coder_prototype/search/__init__.py
```

### Inspect the runtime and fidelity helper docs

```bash
sed -n '1,220p' docs/concepts/dag-runtime-v1-search-surface.md
sed -n '1,220p' docs/concepts/dag-runtime-v3-fidelity-helper-layer.md
```

### Run the DAG/search test slice

```bash
PYTHONPATH=. pytest -q tests/test_search_runtime.py
```

### What this tells you

- search truth, assessment truth, and fidelity helper layers are all now first-class
- the runtime stayed small while replication and comparison structure moved into helper artifacts
- the DAG lane is real enough to test independently

---

## Walkthrough 4: RL export and adapterization

### Read first

- [RL Training System Overview](rl-training-system-overview.md)
- [RL Adapterization Playbook](rl-adapterization-playbook.md)

### Inspect the RL surface

```bash
sed -n '1,260p' agentic_coder_prototype/rl/__init__.py
```

### Inspect the RL docs that define the current maturity line

```bash
sed -n '1,220p' docs/concepts/rl-training-primitives-v1-boundary-and-contract-pack.md
sed -n '1,220p' docs/concepts/rl-training-primitives-v2-export-conformance.md
sed -n '1,220p' docs/concepts/rl-training-primitives-v2-adapter-probes.md
```

### Run the RL test slice

```bash
PYTHONPATH=. pytest -q tests/test_rl_training_primitives.py
```

### Run DAG + RL together

```bash
PYTHONPATH=. pytest -q tests/test_search_runtime.py tests/test_rl_training_primitives.py
```

### What this tells you

- RL is a real overlay, not a loose plan
- replay/live parity and conformance are not afterthoughts
- the DAG-to-RL boundary is directly testable

---

## Walkthrough 5: C-Trees

### Read first

- [C-Trees System Overview](ctrees-system-overview.md)
- [C-Trees Lifecycle Guide](ctrees-lifecycle-guide.md)

### Inspect the package shape

```bash
sed -n '1,220p' agentic_coder_prototype/ctrees/schema.py
sed -n '1,220p' agentic_coder_prototype/ctrees/phase_machine.py
sed -n '1,220p' agentic_coder_prototype/ctrees/branch_receipt_contract.py
sed -n '1,220p' agentic_coder_prototype/ctrees/finish_closure_contract.py
```

### Then inspect the broader family

```bash
ls agentic_coder_prototype/ctrees
```

### What this tells you

- C-Trees are much richer than a single schema
- phase, branch-receipt, and closure discipline are central to the lane
- the package is a research program in its own right, not just a stub

---

## Walkthrough 6: DARWIN

### Read first

- [DARWIN System Overview](darwin-system-overview.md)
- [DARWIN Campaign Workflow Guide](darwin-campaign-workflow-guide.md)
- [DARWIN Contract Pack README](../contracts/darwin/README.md)

### Inspect the contract layer

```bash
sed -n '1,240p' docs/contracts/darwin/README.md
sed -n '1,220p' breadboard_ext/darwin/contracts.py
```

### Inspect the canonical registries

```bash
cat docs/contracts/darwin/registries/lane_registry_v0.json
cat docs/contracts/darwin/registries/policy_registry_v0.json
```

### What this tells you

- DARWIN is artifact- and policy-centered
- the outer-loop layer is typed rather than only note-driven
- campaign and lane governance are already real contracts, not just ideas

---

## Walkthrough 7: how the systems compose

If you want to inspect the interfaces rather than one subsystem at a time:

### DAG to optimization

Read:

- [DAG Runtime V2 optimize adapter note](dag-runtime-v2-optimize-adapter-note.md)
- [Optimization Study Playbook](optimization-study-playbook.md)

### DAG to RL

Read:

- [DAG Runtime V2 RL-facing note](dag-runtime-v2-rl-facing-note.md)
- [RL Training System Overview](rl-training-system-overview.md)

### optimization to DARWIN

Read:

- [Optimization V6 stop/go synthesis](../internals/research/optimization-v6-stop-go-synthesis.md)
- [DARWIN System Overview](darwin-system-overview.md)

### ATP to sandboxing

Read:

- [ATP and Lean Sandboxing](atp-and-lean-sandboxing.md)
- [Sandbox Envelopes V1](../contracts/kernel/semantics/sandbox_envelopes_v1.md)

---

## Recommended “I have one hour” paths

### One hour on ATP

1. read the ATP overview and workflow guide
2. inspect the ATP README and router
3. inspect `run_bb_atp_adapter_slice_v1.py`

### One hour on optimization

1. read the overview and playbook
2. inspect `agentic_coder_prototype/optimize/__init__.py`
3. run the focused optimization tests

### One hour on DAG + RL

1. read the DAG and RL overviews
2. inspect both `__init__.py` files
3. run the search and RL tests together

### One hour on DARWIN

1. read the overview and workflow guide
2. inspect the DARWIN contract README
3. inspect `breadboard_ext/darwin/contracts.py`

---

## Final perspective

The point of these walkthroughs is not to replace the subsystem docs. It is to lower the activation energy.

BreadBoard now has enough serious research infrastructure that readers need:

- a conceptual map
- a composition map
- and a concrete commands-and-files path

With this page, the repo now has all three.
