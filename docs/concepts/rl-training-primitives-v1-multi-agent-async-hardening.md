RL Training Primitives V1 Phase 4 hardens the RL overlay against real BreadBoard workload shapes rather than only single-track traces.

This tranche does not add new DAG kernel nouns. It extends the RL projection layer so the existing truth can be represented more honestly:

- branch-track spawn edges
- branch join/discard edges
- message visibility edges
- workspace visibility edges
- workspace snapshot write edges
- delayed evaluator wake-up edges
- explicit `CreditFrame` attribution
- continuation alignment through checkpoint metadata pointers

The important point is that these remain projection-layer semantics. DAG/search runtime truth stays canonical.

The new `CreditFrame` is deliberately narrow. It is not a trainer algorithm and it is not a reward model. It is an attribution surface that makes async and shared-workspace responsibility inspectable:

- which annotations are being attributed
- which decisions carry weight
- which tracks carry weight
- which workspace refs are in scope
- which annotations arrived late

Continuation alignment is also explicit. RL projection can now carry a portable checkpoint pointer so resumed or resumed-for-verification trajectories stay provenance-aligned with LongRun without turning RL into a controller.

This preserves the V1 discipline:

- no trainer-specific optimizer state in kernel truth
- no transcript-as-native-RL-truth fallback
- no RL-owned duplicate search ontology
- no LongRun controller semantics absorbed into RL
