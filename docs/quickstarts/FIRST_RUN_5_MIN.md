# BreadBoard: first run (5 minutes)

Clone-to-running in the shortest path. Every command below is copy-pasteable.

---

## Prerequisites

- Python 3.11+
- Node.js 20+ (22 recommended)
- `git`

---

## 1. Bootstrap

From the repo root:

```bash
bash scripts/dev/bootstrap_first_time.sh
```

Engine-only (if you don't need the TUI):

```bash
bash scripts/dev/bootstrap_first_time.sh --profile engine
```

What this does:

- creates or reuses `.venv` (prefers `uv` when available)
- installs Python dependencies from `requirements.txt`
- builds `sdk/ts` and `tui_skeleton`
- installs the local `breadboard`/`bb` CLI wrapper
- runs first-time doctor

Subsequent runs are incremental—Python and Node installs are skipped when lockfiles and outputs are unchanged.

---

## 2. Verify setup health

```bash
python scripts/dev/first_time_doctor.py --strict
```

Via CLI (if your build includes first-time doctor flags):

```bash
breadboard doctor --first-time
breadboard doctor --first-time --first-time-profile engine
```

Engine-only fallback (always available):

```bash
python scripts/dev/first_time_doctor.py --profile engine --strict
```

---

## 3. Quick CLI check

```bash
breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/opencode_mock_c_fs.yaml "Reply exactly with: OK"
```

---

## 4. Launch the interactive TUI

```bash
breadboard ui --config agent_configs/opencode_mock_c_fs.yaml
```

---

## 5. Verify SDK paths

Start a local engine in one shell:

```bash
./.venv/bin/python -m agentic_coder_prototype.api.cli_bridge.server
```

In another shell:

```bash
python scripts/dev/python_sdk_hello.py
node scripts/dev/ts_sdk_hello.mjs
```

Or one-command (starts a temporary local engine, then tears it down):

```bash
make sdk-hello-live
make onboarding-contract
```

Engine-only variant:

```bash
bash scripts/dev/sdk_hello_live_smoke.sh --no-ts
```

---

## 6. Daily commands

```bash
# strict environment check
make doctor

# stricter TUI-inclusive checks
make doctor-full
make doctor-tui

# repair the local CLI wrapper if broken
make repair-cli

# unit-only advanced smoke
make smoke

# setup variants
make setup                  # full bootstrap
make setup-fast             # skip doctor
make setup-fast-engine      # engine only, skip doctor
make setup-refresh-python   # force Python dependency reinstall

# full sequential confidence pass + JSON report
make devx-full-pass
make devx-full-pass-engine  # engine-only variant
make devx-timing            # summarize latest timing report

# disk footprint (dry-run)
make disk-report
```

---

## Troubleshooting

**`breadboard: command not found`**

```bash
make repair-cli
# or
bash scripts/dev/repair_cli_wrapper.sh
```

Ensure `~/.local/bin` is on `PATH`, then run `hash -r`.

**`.venv` missing or stale**

```bash
bash scripts/dev/bootstrap_first_time.sh --recreate-venv
```

**Need only one side (engine or TUI)**

Engine/SDK only:

```bash
bash scripts/dev/bootstrap_first_time.sh --skip-node
```

TUI only:

```bash
bash scripts/dev/bootstrap_first_time.sh --skip-python
```

**Low-cost regression check**

```bash
./scripts/smoke_switcher_fancy_use_cases.sh --no-live
```

---

For the full setup reference with all flags, profiles, and advanced smokes, see [`../INSTALL_AND_DEV_QUICKSTART.md`](../INSTALL_AND_DEV_QUICKSTART.md).
