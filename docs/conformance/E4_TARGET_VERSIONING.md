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

## Update procedure when target harness changes

1. Pull/update upstream harness reference repositories.
2. Capture new evidence:
   - tmux nightly provider scenario captures for interactive parity lanes.
   - replay session dumps for OpenCode/other replay lanes.
3. Add/adjust manifest rows with:
   - upstream commit + date,
   - release label,
   - updated evidence paths.
4. Update E4 config behavior only as required for parity.
5. Re-run E4 checks and conformance runs.
6. Record the bump in release notes / parity docs.

## Required E4 config header convention

Each E4 config should include:

```yaml
# e4_target_key: <config_stem>
# e4_target_manifest: config/e4_target_freeze_manifest.yaml
```

This is non-functional metadata for maintainers and reviewers.
