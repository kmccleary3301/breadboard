# E4 capture-a-harness cookbook v2

This guide describes the path for adding a new E4 target-support lane without editing accepted score artifacts by hand. A lane starts as data under `config/e4_lanes/`, then builders and validators consume that data.

## Inputs

A new lane needs these facts before it can move past scaffold status:

- `lane_id`: stable snake-case lane name.
- `config_id`: versioned config ID, usually `${lane_id}_v1`.
- `target_family` and `target_version`: values already registered under `contracts/kernel/registries/target_families.v1.json` and the target dossier.
- `provider_model`: `no-provider` for provider-free lanes, otherwise the exact model name used in target evidence.
- `sandbox_mode`: the execution mode proved by the lane evidence.
- `capture.argv`: the command that generates the lane artifact when the lane has a real builder.
- `reverify_command.argv`: the command that validates the support claim and evidence manifest.

## Scaffold the lane

Run the scaffold into the evidence scratch area first:

```bash
.venv/bin/python scripts/e4_parity/scaffold_e4_target_lane.py \
  --lane-id my_target_feature_lane \
  --config-id my_target_feature_lane_v1 \
  --target-family oh_my_pi \
  --target-version sample \
  --provider-model no-provider \
  --sandbox-mode read_only \
  --emit-inventory-row \
  --emit-lane-def \
  --emit-builder-skeleton \
  --emit-comparator-skeleton \
  --dry-run-json
```

Write mode uses the same flags without `--dry-run-json`. The script refuses accepted evidence roots and writes under `../docs_tmp/phase_15/lane_scaffolds/<lane_id>`.

## Promote scaffold data into lane data

Copy only the reviewed lane definition into `config/e4_lanes/<lane_id>.yaml`. Keep `status: scaffolded` and `points: 0` until the target evidence, support claim, evidence manifest, CT row, and score ledger row all validate.

The scaffolded lane definition is a draft. Before promotion, replace scaffold values with real values:

- `capture.argv` points to the real builder or probe command.
- `claim.scope.behaviors` names the exact primitive behavior under test.
- `compare.config.assertions` checks behavior, scope, provider, target version, and error shape.
- `artifacts_root` points to the lane's governed artifact root.
- `reverify_command` includes `--support-claim`, `--evidence-manifest`, `--json-out`, and `--check-only`.

## Generate and check derived rows

After adding or editing a lane definition, regenerate the derived inventory view and compare it with the canonical inventory:

```bash
.venv/bin/python scripts/e4_parity/generate_lane_inventory.py \
  --out ../docs_tmp/phase_17/BB_NS_LANE_INVENTORY_FROM_DEFS.json \
  --report ../docs_tmp/phase_17/BB_NS_LANE_INVENTORY_CONSISTENCY.json \
  --check
```

The consistency report must have `ok: true`. A mismatch means the lane definition and canonical inventory disagree on a derivable field such as `config_id`, `claim_id`, `status`, `points`, builder command, CT command, or reverify command.

## Run the lane without touching accepted roots

Use `run_lane.py` for stage execution. Write stage outputs to a scratch path unless the lane is already accepted and the command is the governed regeneration command.

```bash
.venv/bin/python scripts/e4_parity/run_lane.py \
  --lane-id my_target_feature_lane \
  --stage claim \
  --json-out /tmp/my_target_feature_lane_claim.json
```

For byte-parity work, compare the scratch output with the accepted artifact. Do not overwrite support claims, score ledgers, CT rows, or canonical manifests as part of exploration.

## Validate before changing score state

A lane can become accepted only after these checks pass:

```bash
.venv/bin/python scripts/validate_e4_c4_chain.py \
  --config-id my_target_feature_lane_v1 \
  --support-claim docs/conformance/support_claims/my_target_feature_lane_v1_c4_support_claim.json \
  --evidence-manifest docs/conformance/support_claims/my_target_feature_lane_v1_c4_evidence_manifest.json \
  --json-out artifacts/conformance/node_gate/ct_my_target_feature_lane.json \
  --check-only

.venv/bin/python scripts/e4_parity/generate_ct_rows.py \
  --out /tmp/ct_rows.json \
  --check

.venv/bin/python scripts/e4_parity/evidence_roots.py \
  scripts/e4_parity tests/e4_parity \
  --baseline ../docs_tmp/phase_17/BB_NS_DOCS_TMP_LITERAL_BASELINE.json \
  --check \
  --json-out /tmp/bb_evidence_roots_lint.json
```

Then run the local E4 parity suite:

```bash
.venv/bin/python -m pytest tests/e4_parity -q
```

Only then update score rows, accepted status, support claims, and final packet artifacts.
