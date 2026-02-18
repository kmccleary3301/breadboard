# Replay Proof Bundle Quickstart

Goal: produce a compact, reproducible "proof bundle" showing that a local run can be executed and inspected via stable artifacts/contracts.

## Prerequisites

- Repo dependencies installed.
- Node/TUI build available.
- Python environment available.

Reference setup:

- `docs/INSTALL_AND_DEV_QUICKSTART.md`

## Step 1: Build TUI/CLI

```bash
npm -C tui_skeleton ci
npm -C tui_skeleton run build
```

## Step 2: Export Contract Artifacts

```bash
python scripts/export_cli_bridge_contracts.py
```

Expected outputs include:

- `docs/contracts/cli_bridge/openapi.json`
- `docs/contracts/cli_bridge/schemas/*.schema.json`

## Step 3: Run Local Smoke

```bash
bash scripts/phase12_live_smoke.sh
```

This validates:

- doctor path,
- run path,
- basic end-to-end local engine/TUI CLI wiring.

## Step 4: Capture a Run Directory

After smoke, identify the latest run under `logging/`:

```bash
ls -1dt logging/* | head -n 1
```

Inspect key artifacts (example):

```bash
RUN_DIR="$(ls -1dt logging/* | head -n 1)"
ls -la "${RUN_DIR}/meta"
```

Depending on logging format/version, inspect one or more of:

- `${RUN_DIR}/meta/run_summary.json`
- `${RUN_DIR}/meta/conversation_ir.json`
- `${RUN_DIR}/meta/telemetry.jsonl`
- `${RUN_DIR}/meta/events.jsonl` (when present)

## Step 5: Generate Reduced Transcript Summary

```bash
python scripts/log_reduce.py "${RUN_DIR}" --turn-limit 2 --tool-only > docs/media/proof/launch_v1/log_reduce_sample_v1.txt
```

## Step 6: Optional Provider Incident Bundle

```bash
python scripts/build_vendor_bundle.py "${RUN_DIR}"
```

## Suggested "Proof Bundle" Checklist

- [ ] `export_cli_bridge_contracts.py` ran successfully.
- [ ] `phase12_live_smoke.sh` passed.
- [ ] latest run dir identified.
- [ ] at least one canonical run artifact present under `meta/` (`run_summary.json`, `conversation_ir.json`, `telemetry.jsonl`, or `events.jsonl`).
- [ ] reduced summary generated (`log_reduce.py`).
- [ ] (optional) vendor bundle generated.

## Notes

- Keep claims conservative and tied to reproducible steps.
- Align public wording with `docs/CLAIMS_EVIDENCE_LEDGER.md`.
