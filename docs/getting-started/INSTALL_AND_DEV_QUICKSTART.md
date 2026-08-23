# Install & dev quickstart

This is the full setup reference for maintainers working on BreadBoard (engine + TUI/CLI). For a minimal copy-paste path, see [../quickstarts/FIRST_RUN_5_MIN.md](../quickstarts/FIRST_RUN_5_MIN.md).

---

## Prerequisites

- Python 3.11+ (3.12 is fine)
- Node.js 20+ (22+ recommended)

---

## Canonical local interpreter

After bootstrap, use the repo-local venv interpreter:

```bash
./.venv/bin/python
```

To launch the engine directly from source:

```bash
./.venv/bin/python -m breadboard_engine.api.cli_bridge.server
```

Older scripts that reference `agentic_coder_prototype.*` still work: that
package is now a compatibility shim that resolves to `breadboard_engine`
(set `BREADBOARD_LEGACY_IMPORTS=warn` or `error` to surface stragglers).

---

## First-time setup

### One-command bootstrap (recommended)

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
- builds `sdk/ts` and the legacy `tui_skeleton` contract harness when present
- does not install `bb`; obtain the product TUI separately from
  [`kmccleary3301/breadboard-tui`](https://github.com/kmccleary3301/breadboard-tui)
- runs the first-time setup doctor

The script is incremental on repeat runs:

- Python dependency reinstall is skipped when `requirements.txt`, interpreter, and resolver are unchanged
- `npm ci` is skipped when `package-lock.json` and Node version are unchanged
- Node builds are skipped when `dist` outputs are newer than sources

### Make shortcuts

```bash
make setup              # full bootstrap
make setup-fast         # full bootstrap, skip doctor checks
make setup-engine       # engine only
make setup-fast-engine  # engine only, skip doctor checks
make setup-refresh-python   # force Python dependency reinstall in existing .venv
make setup-tui          # retained legacy contract-harness profile
make setup-all          # full bootstrap + confidence pass
```

### Bootstrap flags

```bash
# Unit-only advanced smoke lane
bash scripts/dev/bootstrap_first_time.sh --smoke

# Recreate .venv from scratch
bash scripts/dev/bootstrap_first_time.sh --recreate-venv

# Force reinstall Python dependencies
bash scripts/dev/bootstrap_first_time.sh --refresh-python-deps

# Skip Python or Node/legacy-harness setup
bash scripts/dev/bootstrap_first_time.sh --skip-node
bash scripts/dev/bootstrap_first_time.sh --skip-python

# Full confidence pass (unit smoke + live SDK verification)
bash scripts/dev/bootstrap_first_time.sh --all-checks
```

### Status snapshot (before or after setup)

```bash
python scripts/dev/quickstart_first_time.py --include-advanced
python scripts/dev/first_time_doctor.py --strict
make cli-capabilities
make setup-fast
make setup-fast-engine
make setup-refresh-python
make devx-full-pass
make devx-timing
make disk-report
```

The ordinary product CLI begins after setup:

```bash
breadboard --json system health
```

---

## Verifying setup health

```bash
python scripts/dev/first_time_doctor.py --strict
python scripts/dev/first_time_doctor.py --profile engine --strict
```

Verify the installed product CLI:

```bash
breadboard --json system health
breadboard --json system describe
```

---

## Primary TUI

The canonical end-user TUI and `bb` binary are maintained in
[`kmccleary3301/breadboard-tui`](https://github.com/kmccleary3301/breadboard-tui).
That repository consumes the packed `@breadboard/sdk` contract and owns TUI
builds, upstream OMP convergence, and distribution.

## Legacy contract harness

The in-repo `tui_skeleton` client remains available for engine contract,
projection, replay, and golden validation:

```bash
npm -C tui_skeleton ci
npm -C tui_skeleton run typecheck
npm -C tui_skeleton test
npm -C tui_skeleton run build
```

It does not install or own the `bb` or `breadboard` commands. Run it directly
with `npm -C tui_skeleton start -- --help` only when validating its retained
contract surface.


---

## Running BreadBoard

The installed `breadboard` command exposes the product `system`, `harness`,
`harness-lock`, `session`, `integration`, and `artifact` namespaces.

Create, validate, lock, and run a harness:

```bash
breadboard harness create --out ./harness
breadboard harness validate ./harness/minimal_harness.v2.yaml
breadboard harness lock ./harness/minimal_harness.v2.yaml \
  --out ./harness/minimal_harness.lock.json
breadboard harness run ./harness/minimal_harness.v2.yaml \
  --lock ./harness/minimal_harness.lock.json \
  --local \
  --task "Say hi and exit."
```

Inspect the installed product contract:

```bash
breadboard --json system describe
breadboard system schemas
```

### Useful environment toggles

| Variable | Effect |
|----------|--------|
| `RAY_SCE_LOCAL_MODE=1` | Skip Ray initialization during local engine startup |
| `BREADBOARD_ENGINE_KEEPALIVE=1` | Keep a locally launched engine alive after a client exits |
| `BREADBOARD_PROTOCOL_STRICT=1` | Fail if the engine protocol does not match the client |
| `BREADBOARD_SANDBOX_DRIVER=light\|docker\|process` | Override sandbox driver selection |
| `BREADBOARD_DOCKER_NETWORK=<name>` | Select the Docker network used by sandboxed runs |

## Provider credentials

The bundled minimal harness uses `mock/reference` and needs no provider
credentials. Real provider adapters read their credentials from the runtime
environment; do not put tokens in CLI arguments, harness definitions, locks, or
session records.

---

## SDK verification

Start the product API in one shell:

```bash
./.venv/bin/python -m breadboard_engine.api.cli_bridge.server
```

The Python and TypeScript SDK examples in the repository root use the same
lock-first session lifecycle. Verify both installed clients with:

```bash
python -m pytest tests/test_sdk_v1_default_server_smoke.py -q
(cd sdk/ts && npm test)
```

---

## Confidence and timing passes

```bash
# devx smoke (fast)
make devx-smoke
make devx-smoke-engine   # engine-only while TUI is in flux

# full sequential onboarding confidence pass + JSON report
make devx-full-pass
make devx-full-pass-engine

# summarize latest full-pass timing report
make devx-timing
```

---

## Advanced smoke (switcher-style)

```bash
# unit-only advanced migration checks
./scripts/smoke_switcher_fancy_use_cases.sh --no-live

# full local live pass: Python SDK + TypeScript SDK
./scripts/smoke_switcher_fancy_use_cases.sh

# optional tiny real-provider lane (loads OPENAI_API_KEY from .env if unset)
./scripts/smoke_switcher_fancy_use_cases.sh --live-provider

# optional toggles
./scripts/smoke_switcher_fancy_use_cases.sh --no-live-ts
./scripts/smoke_switcher_fancy_use_cases.sh --no-live-auth
./scripts/smoke_switcher_fancy_use_cases.sh --no-live-provider
```

Notes:

- The script auto-selects a free localhost port to avoid collisions.
- The live-provider lane is budget-minimal (`max_steps=1`, no-tool prompt).

---

## Packaging smokes

### Live smoke (source engine)

```bash
bash scripts/phase12_live_smoke.sh
```


---

## Running tests

Python:

```bash
python -m pytest -q tests/test_cli_bridge_contract_exports.py
```

TUI:

```bash
npm -C tui_skeleton test
```

Doctor (strict):

```bash
make doctor
make doctor-full   # stricter TUI-inclusive checks
make doctor-tui
```

---

## Disk maintenance

Local engine artifacts and logs are stored in `~/.breadboard`. If they grow too large, use the retention-safe prune tool.

Dry-run (inspect footprint):

```bash
python scripts/prune_breadboard_home.py
# or
make disk-report
```

Apply cleanup:

```bash
python scripts/prune_breadboard_home.py --apply
# or
make disk-prune
```

Common options:

- `--keep-name local-dev` (default keep)
- `--keep-latest 1` (keep most recent version dir)
- `--remove-tarballs-in-kept` (also remove `*.tar.gz` from kept dirs)
- `--json-out artifacts/maintenance/prune_breadboard_home.json`

---

## Engine CLI repair

The `breadboard` command is owned by the Python package, not the legacy TUI
contract harness. Reinstall its editable entrypoint in `.venv` with:

```bash
make repair-cli
source .venv/bin/activate
breadboard --help
```
