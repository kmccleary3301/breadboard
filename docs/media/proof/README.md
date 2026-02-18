# Launch Proof Media (Deterministic)

This directory holds launch-facing proof media generated from a deterministic
tmux capture run (no live runtime execution required).

## Assets

- Screenshot: `launch_v1/launch_tui_screenshot_v1.png`
- Short clip (MP4): `launch_v1/launch_tui_clip_v1.mp4`
- Short clip (GIF): `launch_v1/launch_tui_clip_v1.gif`
- Source metadata: `launch_v1/launch_tui_capture_source_v1.json`
- Integrity checks: `launch_v1/checksums.sha256`
- Compact bundle zip: `bundles/launch_proof_bundle_v1.zip`

## Deterministic Build Command

From repo root:

```bash
python scripts/build_launch_media_bundle.py \
  --run-dir ../docs_tmp/tmux_captures/scenarios/codex/e2e_compact_semantic_v1/20260210-131357 \
  --screenshot-frame 44 \
  --clip-start 36 \
  --clip-end 45 \
  --fps 4
```

Notes:

- Input is a pre-recorded capture run under `docs_tmp/`.
- The script does not start the engine or run interactive tasks.
- Re-running this command refreshes all launch media artifacts and the bundle zip.

## Bundle Contents

`bundles/launch_proof_bundle_v1.zip` includes:

- media artifacts (screenshot + clip),
- scenario metadata (`scenario_manifest.json`, `run_summary.json`, `meta.json`),
- comparison artifacts (`comparison_report.md`, `comparison_report.json`),
- action trace (`actions.json`),
- source terminal snapshot (`initial.txt`),
- checksums (`checksums.sha256`).

