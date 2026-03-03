# Evidence Bundle v2 + Claim Ledger v1 Scaffold

Date: 2026-02-24

## Scope

This is the initial scaffold for P3 evidence rigor:

- Evidence bundle manifest schema draft (`v2`)
- Claim ledger schema draft (`v1`)
- Claim packet layout + reproduction contract
- Claim linkage rules and exploratory no-claim policy

Schemas:

- `docs/contracts/benchmarks/schemas/evidence_bundle_manifest_v2.schema.json`
- `docs/contracts/benchmarks/schemas/claim_ledger_v1.schema.json`

## Claim Packet Layout (Scaffold)

Recommended packet directory layout:

```text
artifacts/evidence_packets/<claim_id>/
  claim.yaml
  manifest.json
  artifacts/
    ...
  schemas/
    evidence_bundle_manifest_v2.schema.json
    claim_ledger_v1.schema.json
  reproduce/
    README.md
```

### `claim.yaml` required fields

- `claim_id`
- `title`
- `status`
- `tier`
- `owner`
- `evidence_bundle_id`
- `hypothesis`
- `metric_targets` (object)

### `manifest.json` required fields (v2)

- `schema_id = breadboard.evidence_bundle_manifest.v2`
- `bundle_id`
- `claim_ids`
- `tier`
- `reproduction.commands` (non-empty)
- `reproduction.expected_outputs` (non-empty)
- `artifacts` (non-empty)

## Reproduction Command Contract

`reproduction.commands` must be a list of shell commands that can run in isolation from repo root with pinned inputs.

Shape requirements:

1. Each command is standalone and deterministic under declared seed/input snapshots.
2. The full command list must regenerate artifacts referenced in `reproduction.expected_outputs`.
3. Commands should avoid hidden dependencies (undeclared env vars, mutable external state) unless explicitly documented.

## Claim Ledger Linkage Rules

1. Every non-exploratory claim in ledger must include `evidence_bundle_id`.
2. Every claim ID in `manifest.claim_ids` must appear in ledger.
3. Claim status transitions:
   - `exploratory -> proposed -> in_review -> accepted`
   - `in_review/proposed/accepted -> retracted|invalidated` when falsification fails.
4. `accepted` claims require at least tier-1 replay evidence.

## Exploratory No-Claim Policy

Exploratory runs that are not intended as capability claims may omit claim registration, but must be explicit:

- ledger includes:
  - `no_claim_mode.enabled = true`
  - `no_claim_mode.reason = "..."`
- evidence bundle can still exist for debugging/audit, but must not be cited as capability proof.

## Integration Intent

This scaffold is intentionally lightweight and can be integrated without changing kernel ABI:

- ATP and EvoLake lanes emit claim packets as extension artifacts.
- KPI/weekly reporting consume ledger status counts (accepted/retracted/invalidated).
- Governance templates reference claim IDs for merge-time traceability.
