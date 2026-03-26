# RL Training Primitives V2 Pressure-Study Packet

`RL V2 Phase 4` is where the RL overlay has to justify itself against weaker baselines.

This tranche stays deliberately narrow:

- no new RL kernel nouns
- no trainer/product frameworkization
- no benchmark ontology

Instead it packages representative workloads and compares:

- transcript-only views
- flattened global-step views
- scalar-reward-only views
- graph-native BreadBoard RL export

The point is not benchmark theater. The point is to answer:

> Does the graph-native, replay-stable, evaluator-rich RL overlay materially preserve information and auditability that the weaker baselines lose?

The experiment-policy side is also explicit:

- default model is `gpt-5.4-mini`
- escalation must be justified and auditable
- baselines must be budget-matched and split-stable

If the graph-native path does not win this packet honestly, RL V2 should simplify rather than grow.
