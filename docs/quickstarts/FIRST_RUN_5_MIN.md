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

Engine-only (skip the TypeScript SDK and legacy contract harness):

```bash
bash scripts/dev/bootstrap_first_time.sh --profile engine
```

The default full profile:

- creates or reuses `.venv` (prefers `uv` when available)
- installs Python dependencies and the Python-owned `breadboard` engine CLI
- builds `sdk/ts` and the legacy `tui_skeleton` contract harness
- does not install `bb`; obtain the product TUI separately from
  [`kmccleary3301/breadboard-tui`](https://github.com/kmccleary3301/breadboard-tui)
- runs first-time doctor

Subsequent runs are incremental—Python and Node installs are skipped when lockfiles and outputs are unchanged.

---

## 2. Verify setup health

```bash
python scripts/dev/first_time_doctor.py --strict
```

Verify the installed engine catalog:

```bash
breadboard --json system describe
```

The response's `data.default_profile` records the installed package reference,
definition digest, effective lock digest, prompt-resource digest, and
model-role-resource digest. A typed `default_profile_unavailable` or
`default_profile_invalid` response means the installed distribution must be
repaired before startup.

Engine-only fallback (always available):

```bash
python scripts/dev/first_time_doctor.py --profile engine --strict
```

---

## 3. Create and run a harness

```bash
breadboard harness create --out ./harness
breadboard harness validate ./harness/daily_driver.v1.yaml
breadboard harness lock ./harness/daily_driver.v1.yaml \
  --out ./harness/daily_driver.lock.json
breadboard harness run ./harness/daily_driver.v1.yaml \
  --lock ./harness/daily_driver.lock.json \
  --local \
  --task "Reply exactly with: OK"
```

The public session event stream records lifecycle events plus minimal
`assistant_message`, `tool_call`, and `tool_result` observations. Those
observation payloads contain only `metadata.has_content`, the public tool name,
or the public tool name plus an error flag; they never persist assistant content,
tool arguments/results, credentials, paths, or backend event IDs. A completed
stream remains readable after the service restarts.

### Provider operation modes

- **Provider-free:** the copied profile uses `mock/reference` through
  `mock_chat`. It is deterministic and does not consult an environment secret
  or provider credential.
- **Configured provider:** replace `providers.default_model` and the matching
  `providers.models` row with an integration reported as supported by
  `breadboard integration list`, then validate and create a new lock. The
  profile does not turn a native or evidence-only adapter into a supported
  product integration.

Both modes expose the same bounded file/edit/todo tool surface. Shell execution
is enabled but `permissions.options.mode: prompt` and
`permissions.shell.default: ask` require an operator decision.

---

## 4. Start the product API


Start a local product API in one shell:

```bash
./.venv/bin/python -m breadboard_engine.api.cli_bridge.server
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

# stricter legacy TUI contract-harness checks
make doctor-full
make doctor-tui

# reinstall the Python-owned breadboard CLI in .venv
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
source .venv/bin/activate
breadboard --help
```

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
