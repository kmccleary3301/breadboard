# BreadBoard First Run (5 Minutes)

This is the fastest, lowest-friction path from clone to a working local BreadBoard setup.

## 0) Prerequisites

- Python `3.11+`
- Node `20+` (22 recommended)
- `git`

Canonical local interpreter after setup:

```bash
./.venv/bin/python
```

## 1) Bootstrap once

From repo root:

```bash
bash scripts/dev/bootstrap_first_time.sh
```

Optional status snapshot (before/after setup):

```bash
python scripts/dev/quickstart_first_time.py --include-advanced
make cli-capabilities
```

If `breadboard` is installed and healthy on PATH:

```bash
breadboard quickstart --include-advanced
```

If `breadboard` is already on `PATH`, same flow:

```bash
breadboard setup
breadboard setup --sdk-hello-live
breadboard setup --all-checks
breadboard setup --profile engine
breadboard setup --profile tui
```

If `tui_skeleton` sources are intentionally absent in this checkout:

```bash
bash scripts/dev/bootstrap_first_time.sh --profile engine
```

What this does:
- creates/reuses `.venv` (prefers `uv` when available),
- installs Python dependencies from `requirements.txt`,
- installs/builds `sdk/ts`,
- installs/builds `tui_skeleton`,
- skips repeated Python/Node installs when requirements or lockfiles are unchanged,
- skips repeated Node builds when `dist` outputs are newer than sources,
- installs local CLI wrapper (`breadboard` / `bb`),
- runs first-time setup doctor.

## 2) Verify setup health

```bash
python scripts/dev/first_time_doctor.py --strict
```

Or via CLI (if your build supports first-time doctor flags):

```bash
breadboard doctor --first-time
breadboard doctor --first-time --first-time-profile engine
breadboard doctor --first-time --first-time-profile tui
```

Fallback that always works:

```bash
python scripts/dev/first_time_doctor.py --profile engine --strict
```

## 3) Run quick CLI checks

```bash
breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/opencode_mock_c_fs.yaml "Reply exactly with: OK"
```

## 4) Launch the interactive UI

```bash
breadboard ui --config agent_configs/opencode_mock_c_fs.yaml
```

## 5) Verify SDK usage (Python + TypeScript)

Start local engine in one shell:

```bash
./.venv/bin/python -m agentic_coder_prototype.api.cli_bridge.server
```

In another shell (repo root):

```bash
python scripts/dev/python_sdk_hello.py
node scripts/dev/ts_sdk_hello.mjs
```

Or one-command version (starts a temporary local engine):

```bash
make sdk-hello-live
make onboarding-contract
```

Engine-only verification:

```bash
bash scripts/dev/sdk_hello_live_smoke.sh --no-ts
```

## 6) Recommended daily commands

```bash
# strict setup validation
make doctor
# optional stricter TUI-inclusive checks
make doctor-full
make doctor-tui
# repair local CLI wrapper if broken
make repair-cli

# unit-only advanced smoke
make smoke

# full first-time setup
make setup
# fast repeat setup (skip doctor)
make setup-fast
# fast repeat engine-only setup (skip doctor)
make setup-fast-engine
# force Python dependency reinstall in existing .venv
make setup-refresh-python
# full confidence pass
make setup-all
# full sequential onboarding confidence pass + JSON report
make devx-full-pass
# summarize latest full-pass timing report
make devx-timing
# engine-only variant while TUI is in flux
make devx-full-pass-engine
# inspect ~/.breadboard footprint and cleanup plan (dry-run)
make disk-report
```

## Troubleshooting

- `breadboard: command not found`
  - Run `make repair-cli` (or `bash scripts/dev/repair_cli_wrapper.sh`).
  - Ensure `~/.local/bin` is on `PATH`.
  - Run `hash -r`.

- `.venv` missing or stale
  - Recreate with:
    ```bash
    bash scripts/dev/bootstrap_first_time.sh --recreate-venv
    ```

- Need only one side:
  - engine/sdk only:
    ```bash
    bash scripts/dev/bootstrap_first_time.sh --skip-node
    ```
  - tui only:
    ```bash
    bash scripts/dev/bootstrap_first_time.sh --skip-python
    ```

- Low-cost regression pass:
  ```bash
  ./scripts/smoke_switcher_fancy_use_cases.sh --no-live
  ```
