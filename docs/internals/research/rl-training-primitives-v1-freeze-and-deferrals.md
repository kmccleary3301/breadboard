RL Training Primitives V1 closes as a coherent, trainer-neutral overlay over BreadBoard runtime truth.

The final tranche does not expand the graph surface again. It proves the delegated boundaries instead:

- serving / inference provenance probe
- evaluator / verifier annotation probe
- dataset / training-feedback probe

The closeout decision is `freeze_rl_v1`.

That means:

- BreadBoard owns semantic truth and export contracts
- serving fleets remain outside BreadBoard
- evaluator and verifier pools remain outside BreadBoard
- dataset engines and trainer internals remain outside BreadBoard

`TrainingFeedback` is supported only as a narrow, optional return surface from delegated training/data systems. It is not a trainer control plane and it does not smuggle optimizer state into BreadBoard.

The main V2 deferrals remain explicit:

- trainer-specific packing and optimizer state
- counterfactual relabeling packs
- deep online learner control surfaces
- canonical RL-owned search overlay

This keeps RL V1 aligned with the core doctrine:

- graph-native RL truth
- replay-stable export
- explicit provenance
- explicit evaluation truth
- no trainer-mode or paper-mode runtime expansion
