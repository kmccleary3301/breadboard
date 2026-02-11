# TUI Goldens (Ink Scrollback)

This folder contains **deterministic TUI goldens** generated from `events.jsonl` replay inputs.

## Structure

```
misc/tui_goldens/
  manifests/
    tui_goldens.yaml
  scenarios/
    <scenario_id>/
      input/events.jsonl
      blessed/render.txt
      blessed/frame.grid.json
      blessed/meta.json
  artifacts/
    run-<timestamp>/
      <scenario_id>/render.txt
      <scenario_id>/frame.grid.json
      _diffs/
        <scenario_id>.diff.txt
        <scenario_id>.summary.json
        index.json
        # index.json includes counts + per-scenario status
```

## Run (Generate Candidates)

From `tui_skeleton/`:

```bash
node --import tsx scripts/run_tui_goldens.ts \
  --manifest ../misc/tui_goldens/manifests/tui_goldens.yaml \
  --out ../misc/tui_goldens/artifacts
```

## Bless (Promote Candidates → Blessed)

```bash
node --import tsx scripts/bless_tui_goldens.ts \
  --manifest ../misc/tui_goldens/manifests/tui_goldens.yaml \
  --source ../misc/tui_goldens/artifacts/run-<timestamp> \
  --blessed-root ../misc/tui_goldens/scenarios
```

If `frame.grid.json` exists in the candidate run, it is copied into `blessed/` alongside `render.txt`.

## Compare (Report or Strict)

```bash
# report-only
node --import tsx scripts/compare_tui_goldens.ts \
  --manifest ../misc/tui_goldens/manifests/tui_goldens.yaml \
  --candidate ../misc/tui_goldens/artifacts/run-<timestamp> \
  --blessed-root ../misc/tui_goldens/scenarios \
  --summary

# strict (nonzero on mismatch)
node --import tsx scripts/compare_tui_goldens.ts \
  --manifest ../misc/tui_goldens/manifests/tui_goldens.yaml \
  --candidate ../misc/tui_goldens/artifacts/run-<timestamp> \
  --blessed-root ../misc/tui_goldens/scenarios \
  --strict
```

## Report-Only Runner

```bash
./scripts/run_tui_goldens_report.sh
```

This runs the goldens and compares them **without failing** (report-only), writing diffs into `artifacts/run-*/_diffs/`.
The runner also prints a one-line summary (scenario counts) for CI logs.

## Grid Artifacts (Cell-Grid IR)

`frame.grid.json` captures the terminal cell grid for each scenario. You can compare grids directly:

```bash
node --import tsx scripts/compare_tui_goldens_grid.ts \
  --manifest ../misc/tui_goldens/manifests/tui_goldens.yaml \
  --candidate ../misc/tui_goldens/artifacts/run-<timestamp> \
  --blessed-root ../misc/tui_goldens/scenarios \
  --summary
```

You can also mask dynamic content in a grid file:

```bash
node --import tsx scripts/mask_grid.ts --in frame.grid.json --out frame.masked.json
```

## Manifest Defaults + Normalization

`manifests/tui_goldens.yaml` supports a `defaults:` block for render and normalization options. Scenario-level overrides merge on top.

Normalization currently supports:
- `timestamps` (e.g., "Cooked for 3s" → "Cooked for <elapsed>")
- `tokens` (e.g., "tokens: 1,234" → "tokens: <tokens>")
- `paths` (e.g., `/home/...` → `<path>`)
- `status` (e.g., transient statuses → `<status>`)

Event replays also pass through `normalizeSessionEvent` to stabilize transient fields before rendering.

## Golden Versioning

`GOLDEN_VERSION` tracks the current TUI golden version. Bump it when renderer semantics change intentionally.

## CI (Report-Only Hook)

Add a CI step that runs:

```
cd tui_skeleton
./scripts/run_tui_goldens_report.sh
```

This produces artifacts and diff summaries without failing the build.

## Claude Alignment (Report-Only)

Claude alignment compares our TUI render against captured Claude Code reference frames.

Run (from `tui_skeleton/`):

```bash
./scripts/run_claude_compare_report.sh
```

This will:

- Render scenarios from `misc/tui_goldens/manifests/claude_compare_manifest.yaml`.
- Write candidates under `misc/tui_goldens/claude_compare/run-*/`.
- Compare against `misc/tui_goldens/claude_refs/` and emit diffs in `run-*/_diffs/`.

To add new references, capture in Claude Code and drop the results in
`misc/tui_goldens/claude_refs/<scenario_id>/` (see `claude_refs/CAPTURE_CHECKLIST.md`).

## Engine Replay -> TUI Bridge

You can now convert replay-mode parity runs into TUI-ready event fixtures and render them end-to-end.

1. Export replay `events.jsonl` from engine replay runs:

```bash
python scripts/run_engine_replay_and_export_events.py \
  --manifest misc/opencode_runs/parity_scenarios.yaml \
  --tag replay \
  --strict
```

2. Render TUI snapshots from the exported events:

```bash
python scripts/run_e2e_tui_from_engine_replays.py \
  --include-header \
  --include-status \
  --include-hints \
  --colors \
  --unicode \
  --strict
```

Artifacts are written under:

- `misc/tui_goldens/_runs/engine_replay_events/run-*/`
- `misc/tui_goldens/_runs/engine_replay_tui/run-*/`

## Tmux Capture Polling (Unified)

Use these scripts for deterministic panel capture and PNG rendering:

```bash
python scripts/tmux_capture_poll.py \
  --target <session:window.pane> \
  --interval 0.5 \
  --duration 60 \
  --capture-mode pane \
  --png \
  --out-root ../docs_tmp/tmux_captures \
  --scenario <scenario_id>
```

Scripted multi-step captures:

```bash
python scripts/run_tmux_capture_scenario.py \
  --target <session:window.pane> \
  --scenario <family/name> \
  --actions ../docs_tmp/tmux_captures/scenario_actions/<name>.json \
  --duration 180 \
  --interval 0.5 \
  --out-root ../docs_tmp/tmux_captures/scenarios
```

For provider-dump validation, add:

- `--provider-dump-dir <dir>`
- `--provider-fail-on-http-error`
- `--provider-fail-on-model-not-found`
- `--provider-fail-on-permission-error`
