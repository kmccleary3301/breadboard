# DARWIN Contract Pack (V0)

This directory defines the Phase-1 DARWIN control-plane and evaluation-plane contracts that sit above BreadBoard’s general kernel and above the older EvoLake bridge scaffolding.

## Scope

Phase-1 T0 freezes:

- DARWIN naming and legacy `evolake` compatibility posture
- typed campaign / policy / candidate / evaluation / evidence / claim contracts
- lane registry v0
- policy registry v0
- weekly evidence packet template
- claim ladder and evidence-gate policy

## Files

### Human-readable docs

- `DARWIN_CONTRACT_PACK_V0.md`
- `DARWIN_LANE_REGISTRY_V0.md`
- `DARWIN_POLICY_REGISTRY_V0.md`
- `DARWIN_CLAIM_LADDER_V0.md`
- `DARWIN_WEEKLY_EVIDENCE_PACKET_V0.md`

### Machine-readable schemas

- `schemas/campaign_spec_v0.schema.json`
- `schemas/policy_bundle_v0.schema.json`
- `schemas/candidate_artifact_v0.schema.json`
- `schemas/evaluation_record_v0.schema.json`
- `schemas/evidence_bundle_v0.schema.json`
- `schemas/claim_record_v0.schema.json`
- `schemas/lane_registry_v0.schema.json`
- `schemas/policy_registry_v0.schema.json`
- `schemas/weekly_evidence_packet_v0.schema.json`

### Canonical registries

- `registries/lane_registry_v0.json`
- `registries/policy_registry_v0.json`

## Legacy compatibility

DARWIN is the canonical program name. Existing stable `evolake` runtime identifiers, artifact keys, and extension names remain valid until a dedicated migration tranche lands. This pack therefore introduces DARWIN contracts without breaking the existing EvoLake bridge layer.
