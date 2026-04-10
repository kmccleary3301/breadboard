# DARWIN Stage 6 tranche-1 status

Date: `2026-04-09`

Scope landed in this slice:

- `FamilyActivationV1` operationalized in `breadboard_ext/darwin/stage6.py`
- `ComparisonEnvelopeV1` operationalized in `breadboard_ext/darwin/stage6.py`
- `TransferCaseV2` operationalized in `breadboard_ext/darwin/stage6.py`
- `single_family_lockout` support added at the Stage-6 selection and compounding-case layer
- first bounded Systems-primary broader-transfer canary added in `scripts/run_darwin_stage6_broader_transfer_canary_v0.py`

Current read:

- the canary now emits non-empty Stage-6 surfaces
- live canary output currently produces:
  - `4` Stage-6 compounding cases
  - `2` Stage-6 transfer cases
  - `4` `single_family_lockout` comparisons
- live activation probe summary now exists in:
  - `artifacts/darwin/stage6/tranche1/broader_transfer_canary/activation_probe_summary_v1.json`
- provider/route segmentation is now explicit in the canary output:
  - current status: `claim_rows_segmented`
  - requested provider origin: `openrouter`
  - actual provider origin: `openai`
  - execution mode: `live`
  - cost source: `estimated_from_pricing_table`
  - fallback reason: `openrouter_http_401`
- the current live canary is now technically clean and partially positive:
  - Repo_SWE topology challenge:
    - `1` `reuse_lift`
    - `1` `flat`
    - classified `positive_activation_probe`
    - transfer case `lane.repo_swe -> lane.systems` is now `activation_probe`
  - Systems policy primary:
    - `1` `no_lift`
    - `1` `flat`
    - classified `inconclusive_activation_probe`
    - transfer case `lane.systems -> lane.scheduling` remains `descriptive_only`
- Stage-6 activation defaults preserve the current proving split:
  - `lane.systems` primary
  - `lane.repo_swe` challenge
  - `lane.scheduling` bounded transfer target

What is complete in this slice:

- Stage-6 operational logic is no longer builder-only
- `single_family_lockout` is now a typed comparison mode in Stage-6 selection and case generation
- broader-transfer canary output is now real and reproducible in live mode
- provider/route segmentation is canonical on live claim-bearing rows in the canary
- Systems-primary activation policy is now operational, not metadata-only:
  - `single_family_lockout` for `lane.systems` falls back to the control topology/policy envelope
- at least one positive Stage-6 activation probe now exists on the live canary
- at least one Stage-6 transfer case now advances beyond `descriptive_only`

What is not yet complete:

- Systems-primary activation is still not positive
- broader transfer on the Systems-primary path is still the next proving task
