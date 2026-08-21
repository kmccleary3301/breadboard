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

Verify the installed product catalog:

```bash
breadboard --json system describe
```

Engine-only fallback (always available):

```bash
python scripts/dev/first_time_doctor.py --profile engine --strict
```

---

## 3. Create and run a harness

```bash
breadboard harness create --out ./harness
breadboard harness validate ./harness/minimal_harness.v2.yaml
breadboard harness lock ./harness/minimal_harness.v2.yaml \
  --out ./harness/minimal_harness.lock.json
breadboard harness run ./harness/minimal_harness.v2.yaml \
  --lock ./harness/minimal_harness.lock.json \
  --local \
  --task "Reply exactly with: OK"
```

---

## 4. Start the product API


Start a local product API in one shell:

```bash
./.venv/bin/python -m agentic_coder_prototype.api.cli_bridge.server
```

Verify its catalog-backed system endpoint:

```bash
curl http://127.0.0.1:9099/v1/system
```

---

## 5. Verify SDK paths

The repository SDK smoke runs the same lock-first session lifecycle through the
Python and TypeScript installed clients:

```bash
python -m pytest tests/test_sdk_v1_default_server_smoke.py -q
(cd sdk/ts && npm test)
```

---

## 6. Daily commands

```bash
# quick status snapshot
python scripts/dev/quickstart_first_time.py --include-advanced

# strict environment check
python scripts/dev/first_time_doctor.py --strict
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

For the full setup reference with all flags, profiles, and advanced smokes, see [../getting-started/INSTALL_AND_DEV_QUICKSTART.md](../getting-started/INSTALL_AND_DEV_QUICKSTART.md).
