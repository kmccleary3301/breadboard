# DARWIN Stage 6 Tranche 1 review

Date: `2026-04-09`

Question set:

- is family activation operational?
- is provider/route segmentation canonical on live claim-bearing rows?
- does `single_family_lockout` run as a real control rather than metadata-only?
- is there at least one positive activation probe?
- is broader transfer still purely descriptive, or starting to become real?

Findings:

- family activation is operational in `breadboard_ext/darwin/stage6.py`
- provider/route segmentation is canonical on live claim-bearing rows in:
  - `artifacts/darwin/stage6/tranche1/broader_transfer_canary/provider_segmentation_v1.json`
- the Systems-primary `single_family_lockout` control is now execution-real:
  - `activation_policy.mode = systems_primary_control_envelope_lockout_v1`
  - lockout execution falls back to `policy.topology.single_v0`
- the live canary now contains one positive activation probe:
  - Repo_SWE topology challenge family is `positive_activation_probe`
  - reflected in `artifacts/darwin/stage6/tranche1/broader_transfer_canary/activation_probe_summary_v1.json`
- broader transfer is no longer purely descriptive:
  - `lane.repo_swe -> lane.systems` now records `transfer_status = activation_probe`
  - `lane.systems -> lane.scheduling` remains `descriptive_only`

Interpretation:

- Tranche 1 succeeded at its control objective:
  - activation is now operational
  - live segmentation is trustworthy
  - Stage-6 controls can produce a positive activation result without widening runtime truth
- the positive probe currently appears on the challenge lane, not the primary proving lane
- Systems remains the correct primary lane for Stage 6 because:
  - its route/provider surface is still cleaner for controlled proving
  - the Systems family is still the intended proving center
  - but its current activation outcome remains mixed and should be the first focus in Tranche 2

Conclusion:

- Tranche 1 is complete
- the result is strong enough to leave pure activation/economics-truth setup work
- the next correct move is Stage 6 Tranche 2:
  - make broader transfer real on the Systems-primary path
  - keep Repo_SWE as a bounded challenge lane
