# DARWIN Stage 6 Tranche 3 review

Date: `2026-04-09`

Question set:

- is broader compounding now real on top of the retained Systems-primary transfer?
- are economics and comparison surfaces still interpretable?
- is Repo_SWE still a useful challenge lane?
- is the family center stable enough?
- is Stage 6 now ready for the final composition-decision tranche?

Findings:

- broader compounding is now real on top of the retained Systems-primary transfer:
  - scheduling target shows `2/2` positive broader-compounding rounds
  - score lift vs `family_lockout` is `0.218337`
  - score lift vs `single_family_lockout` is `0.399573`
  - reflected in `artifacts/darwin/stage6/tranche3/broader_compounding/broader_compounding_v0.json`
- economics and comparison surfaces remain interpretable:
  - broader-compounding comparisons are bounded to one retained family center
  - provider segmentation remains canonical through the retained-transfer base
  - local scheduling execution is explicit rather than hidden as a live-provider claim
  - reflected in `artifacts/darwin/stage6/tranche3/economics_attribution/economics_attribution_v0.json`
- Repo_SWE remains a useful challenge lane:
  - it stays present as challenge transfer context
  - it does not displace the Systems-primary retained-family center
- the family center is stable enough:
  - current decision is `hold_single_retained_family_center`
  - no second Stage-6 family activation is needed yet
  - reflected in `artifacts/darwin/stage6/tranche3/family_registry/family_registry_v0.json`
- replay posture is good enough for this tranche:
  - retained transfer = `supported`
  - broader compounding over the retained family = `supported`
  - reflected in `artifacts/darwin/stage6/tranche3/replay_posture/replay_posture_v0.json`

Interpretation:

- Tranche 3 succeeded at its actual objective:
  - retained transfer now compounds into later target-lane improvement
  - the improvement is repeated and not merely descriptive
  - the proving surface remains compact and interpretable
- the most important Stage-6 result so far is not transfer by itself:
  - it is that retained transfer now leads into positive broader compounding on the bounded target
- the next Stage-6 question is no longer whether broader compounding exists.
- the next question is whether composition should open at all under a tightly bounded final tranche.

Conclusion:

- Tranche 3 is complete
- Stage 6 is ready to enter Tranche 4:
  - the composition decision tranche
- composition should remain tightly gated and can still resolve to explicit no-go
