# E4 Codex Live Refresh Runbook

This runbook is the stable path for refreshing the Codex E4 live lane evidence.

## Why this path

- Replay targets must **not** run against repo-root workspace (workspace safety guard rejects it).
- Replay targets are more stable on `dist` runtime than `tsx` dev runtime during capture sweeps.

`scripts/start_tmux_phase4_replay_target.sh` now enforces this by default:

- safe workspace default: `/tmp/breadboard_replay_<session>_ws`
- repo-root workspace auto-fallback
- `dist` runtime default (with auto-build if missing)

## 1) Start isolated target

```bash
cd breadboard_repo
bash scripts/start_tmux_phase4_replay_target.sh \
  --session breadboard_test_e4_codex \
  --tmux-socket bb_e4 \
  --cols 160 --rows 45 \
  --port 9261 \
  --tui-preset codex_cli_like \
  --config agent_configs/codex_cli_gpt51mini_e4_live.yaml
```

## 2) Capture the nightly provider scenario

```bash
cd breadboard_repo
python scripts/run_tmux_capture_scenario.py \
  --target breadboard_test_e4_codex:0.0 \
  --tmux-socket bb_e4 \
  --scenario nightly_provider/codex_e2e_compact_semantic_v1 \
  --actions config/tmux_scenario_actions/nightly_provider/codex_e2e_compact_semantic_v1.json \
  --out-root ../docs_tmp/tmux_captures/scenarios \
  --duration 20 \
  --interval 0.5 \
  --capture-mode fullpane \
  --tail-lines 120 \
  --final-tail-lines 0 \
  --fullpane-start-markers "Config:,/tmp/" \
  --fullpane-max-lines 500 \
  --fullpane-render-max-rows 300 \
  --render-profile phase4_locked_v5 \
  --settle-ms 140 \
  --settle-attempts 5 \
  --session-prefix-guard breadboard_test_ \
  --protected-sessions bb_tui_codex_dev,bb_engine_codex_dev,bb_atp
```

Expected output includes:

- `result=pass`
- `action_error=None`

## 3) Mirror evidence files into repo-local expected path

Replace `<RUN_ID>` with the run ID printed by the scenario command.

```bash
mkdir -p docs_tmp/tmux_captures/scenarios/nightly_provider/codex_e2e_compact_semantic_v1/<RUN_ID>
cp -f ../docs_tmp/tmux_captures/scenarios/nightly_provider/codex_e2e_compact_semantic_v1/<RUN_ID>/{meta.json,scenario_manifest.json,run_summary.json} \
  docs_tmp/tmux_captures/scenarios/nightly_provider/codex_e2e_compact_semantic_v1/<RUN_ID>/
```

## 4) Update manifest codex row

Update in `config/e4_target_freeze_manifest.yaml`:

- `harness.upstream_commit`
- `harness.upstream_commit_date`
- `calibration_anchor.run_id`
- codex `calibration_anchor.evidence_paths` to `<RUN_ID>`

## 5) Validate

```bash
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --max-evidence-age-days 45 --json
python scripts/audit_e4_target_drift.py --json-out artifacts/conformance/e4_target_drift_audit_report.json
```

Expected:

- strict checks: `ok: true`
- drift audit: `drift_count: 0` (for the moment of snapshot)

## 6) Cleanup isolated tmux server

```bash
tmux -L bb_e4 kill-server || true
```

