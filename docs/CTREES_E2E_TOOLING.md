# C-Trees E2E Tooling (Engine)

This document describes the **engine-side** tooling for packaging and reviewing
C-Trees runs without relying on the TUI.

## Minimal run bundle

Use `scripts/ctrees_lab_bundle.py` to collect a deterministic, self-contained
bundle from an existing logging run.

Example:

```bash
python scripts/ctrees_lab_bundle.py logging/20260106-023026_ray_SCE
```

Passing log_reduce args:

```bash
python scripts/ctrees_lab_bundle.py logging/20260106-023026_ray_SCE \
  --log-reduce-args "--turn-limit 5 --tool-only"
```

Adding experiment tags:

```bash
python scripts/ctrees_lab_bundle.py logging/20260106-023026_ray_SCE \
  --tags "baseline,policy-default"
```

Optional inputs:
- `--workspace <path>` if `run_summary.json` lacks `workspace_path`
- `--ctrees <path>` to override `.breadboard/ctrees`
- `--eventlog <events.jsonl>` to backfill C-Trees artifacts if missing

Outputs (under `<log_dir>/ctrees_lab_bundle` by default):
- `bundle_manifest.json`
- `meta/run_summary.json` (if available)
- `ctrees/meta/ctree_events.jsonl` + `ctrees/meta/ctree_snapshot.json`
- `ctrees/tree/<stage>.json` (default stage = FROZEN)
- `ctrees/hashes.json`
- `ctrees/selection_deltas.json`
- `review/reduced.md` (from `scripts/log_reduce.py`, unless `--no-log-reduce`)

Notes:
- `--compiler-config <json>` lets you pass a C-Trees compiler config to the tree view.
- `--include-previews` includes sanitized content previews in the tree view metadata.

## ctLab quickstart

Validate a bundle:

```bash
python scripts/ctlab.py validate logging/20260106-023026_ray_SCE/ctrees_lab_bundle
```

Compare two bundles:

```bash
python scripts/ctlab.py diff path/to/bundle_a path/to/bundle_b
```

Fast checks (structure + hashes):

```bash
python scripts/ctlab.py quick path/to/bundle
python scripts/ctlab.py lint path/to/bundle
```

Open the reduced review:

```bash
python scripts/ctlab.py review path/to/bundle
```

Run the gauntlet over all bundles under `logging/`:

```bash
python scripts/ctlab.py gauntlet --root logging --mode quick
```

## Session transcript (session.jsonl)

Render a compact transcript from `session.jsonl`:

```bash
python scripts/session_transcript.py /path/to/session.jsonl --include-custom --include-tool-calls
```

Write output to a file:

```bash
python scripts/session_transcript.py /path/to/session.jsonl --out /tmp/session_transcript.md
```

## Session cost summary (events.jsonl)

Summarize usage and cost from an event log:

```bash
python scripts/session_cost_summary.py --session-dir /path/to/session_dir
```

Optional pricing (JSON map):

```bash
python scripts/session_cost_summary.py \
  --session-dir /path/to/session_dir \
  --pricing pricing.json \
  --model openai/gpt-4.1
```

## Quick debug checklist

1) Inspect `ctrees/hashes.json`:
   - `node_hash` should match the snapshot + compiler hashes.
2) Inspect `ctrees/tree/frozen.json`:
   - Ensure `hashes.tree_sha256` is present.
   - Verify `selection.selection_sha256` is present and stable across re-runs.
3) Confirm C-Trees artifacts exist:
   - `ctrees/meta/ctree_events.jsonl`
   - `ctrees/meta/ctree_snapshot.json`

## Determinism checklist (local)

- Re-run the bundle twice on the same log dir and compare:
  - `ctrees/hashes.json` (node_hash, tree_sha256, z1/z2/z3)
  - `ctrees/tree/frozen.json` selection + hashes
- If hashes differ, check for:
  - volatile keys in payloads (timestamps/seq) not being sanitized
  - nondeterministic ordering (unordered dicts or sets)
  - unstable task ids without aliasing

## Reviewer determinism checklist (snippet)

- Re-run `ctlab validate` on the same bundle twice and confirm hashes are identical.
- Spot-check `ctrees/selection_deltas.json` for unexpected changes in kept/dropped counts.
- Confirm `bundle_manifest.json` captures `git_rev`, `compiler_config_sha256`, and tags.
