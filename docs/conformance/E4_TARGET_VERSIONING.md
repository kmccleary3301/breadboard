# E4 Target Versioning

BreadBoard E4 configs are now version-pinned to explicit upstream harness snapshots via:

- `config/e4_target_freeze_manifest.yaml`

## Why this exists

Target harnesses (Codex CLI, Claude Code, OpenCode, and related variants) evolve quickly.  
Without explicit pinning, E4 can drift silently and parity assertions become ambiguous.

This manifest ensures each E4 config maps to:

1. The upstream harness repo + commit snapshot.
2. A release label used in parity reports.
3. A concrete calibration anchor (tmux capture or replay session dump evidence).

## Scope

Current enforced scope is every file matching:

- `agent_configs/*e4*.yaml`

Each such config must have a corresponding manifest entry.

## Validation

Run:

```bash
make e4-target-manifest
```

Equivalent direct check:

```bash
python scripts/check_e4_target_freeze_manifest.py --json
```

Optional strict evidence check:

```bash
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
```

Optional freshness check (fails stale calibration anchors):

```bash
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json
```

## Refresh helpers

Generate a dry-run update plan from local harness clones in `../other_harness_refs`:

```bash
make e4-target-refresh-plan
```

Equivalent direct command:

```bash
python scripts/update_e4_target_freeze_manifest.py --check --json-out artifacts/conformance/e4_target_refresh_plan.json
```

Apply the refresh in-place:

```bash
python scripts/update_e4_target_freeze_manifest.py --write
```

## Nightly drift audit

A nightly workflow (`.github/workflows/e4-target-drift-audit-nightly.yml`) checks
manifest-pinned commits against upstream remote HEADs and uploads:

- `artifacts/e4_target_drift_audit_report.json`

Local equivalent:

```bash
make e4-target-drift-audit
```

## Update procedure when target harness changes

1. Pull/update upstream harness reference repositories.
2. Generate refresh plan and inspect drift:
   - `make e4-target-refresh-plan`
   - `make e4-target-drift-audit`
3. Capture new evidence:
   - tmux nightly provider scenario captures for interactive parity lanes.
   - replay session dumps for OpenCode/other replay lanes.
4. Add/adjust manifest rows with:
   - upstream commit + date,
   - release label,
   - updated evidence paths.
5. Update E4 config behavior only as required for parity.
6. Re-run E4 checks and conformance runs.
7. Record the bump in release notes / parity docs.

## Required E4 config header convention

Each E4 config should include:

```yaml
# e4_target_key: <config_stem>
# e4_target_manifest: config/e4_target_freeze_manifest.yaml
```

This is non-functional metadata for maintainers and reviewers.
