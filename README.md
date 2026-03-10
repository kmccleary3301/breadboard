<p align="center">
  <img src="docs/media/branding/breadboard_ascii_logo_v1.svg" alt="BreadBoard logo" width="640" />
</p>

<p align="center"><strong>Agent engine · harness emulator · TypeScript backbone · multi-client coding stack.</strong></p>

<p align="center">
  <a href="https://github.com/kmccleary3301/breadboard/actions/workflows/ci.yml">
    <img alt="CI" src="https://github.com/kmccleary3301/breadboard/actions/workflows/ci.yml/badge.svg" />
  </a>
  <a href="https://github.com/kmccleary3301/breadboard/actions/workflows/tmux-e2e-soft-gate.yml">
    <img alt="tmux e2e soft gate" src="https://github.com/kmccleary3301/breadboard/actions/workflows/tmux-e2e-soft-gate.yml/badge.svg" />
  </a>
  <img alt="Version" src="https://img.shields.io/badge/version-launch--v1-7C3AED" />
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" />
  <img alt="Node" src="https://img.shields.io/badge/node-22%2B-5FA04E?logo=node.js&logoColor=white" />
  <img alt="TS backbone" src="https://img.shields.io/badge/typescript-backbone-3178C6?logo=typescript&logoColor=white" />
  <img alt="E4 dossiers" src="https://img.shields.io/badge/public%20E4%20dossiers-4%20harnesses-1f6feb" />
  <img alt="CLI bridge" src="https://img.shields.io/badge/engine-client%20split-HTTP%20%2B%20SSE-0A7EA4" />
</p>

<p align="center">
  <a href="docs/quickstarts/FIRST_RUN_5_MIN.md"><strong>5-minute quickstart</strong></a>
  ·
  <a href="docs/INDEX.md"><strong>docs index</strong></a>
  ·
  <a href="docs/conformance/README.md"><strong>conformance</strong></a>
  ·
  <a href="agent_configs/codex_0-107-0_e4_3-6-2026.yaml"><strong>public E4 dossiers</strong></a>
</p>

<p align="center">
  <img src="docs/media/proof/launch_v1/launch_tui_screenshot_showcase_v1.png" alt="BreadBoard TUI showcase" width="960" />
</p>

BreadBoard is a coding-agent engine and research workbench built around one hard constraint:

> every client, replay, harness profile, and host integration should run against the same engine truth surface rather than a collection of unrelated wrappers

That constraint is what makes the repo unusual.

BreadBoard is simultaneously:

- a Python engine with a CLI bridge and deterministic event log
- Python and TypeScript SDKs over the same bridge
- a terminal-first coding stack with multiple client surfaces
- a harness emulator with public E4 dossier configs for Codex, Claude Code, OpenCode, and oh-my-opencode
- a TypeScript backbone product layer for host apps that do not want to think in raw kernel substrate terms
- a conformance-heavy environment for replay, parity, optimization, long-run agents, and future evaluation work

This repository is for engineers and researchers who care about:

- strong contract boundaries
- reproducibility
- host/runtime interoperability
- evidence-backed parity claims
- configurable harness behavior instead of hard-coded one-off agents

---

## What you can do with BreadBoard

| Surface | What it is | Status |
|---|---|---|
| Engine + CLI bridge | Canonical HTTP + SSE runtime surface used by every client | 🟢 |
| Python SDK | Programmatic access to the engine from Python | 🟢 |
| TypeScript SDK | CLI-bridge TypeScript client package | 🟢 |
| TypeScript backbone | Public TS product layer for serious host apps | 🟢 |
| TUI | Main terminal UX in [`tui_skeleton/`](tui_skeleton/) | 🟢 |
| OpenTUI slab | Fixed-height slab client in [`opentui_slab/`](opentui_slab/) | 🟡 |
| VSCode sidebar | Early client surface in [`vscode_sidebar/`](vscode_sidebar/) | 🟡 |
| Harness emulation | Public dossier configs plus replay/parity tooling | 🟢 |
| Replay / conformance | Golden, parity, and contract validation paths | 🟢 |
| Long-run / RLM / optimization work | Experimental and advancing, but real | 🟡 |

### The shortest accurate description

BreadBoard is what you get if you start with:

- “I want something in the same broad class as Codex CLI, Claude Code, and OpenCode”

and then you keep going until you also have:

- explicit kernel contracts
- replay-proof evidence
- profile-driven harness emulation
- TypeScript host-backbone surfaces
- multiple clients talking to one engine

It is **not** a thin wrapper around one provider SDK, and it is **not** just a terminal app.

---

## Choose your path

| If you want to… | Start here |
|---|---|
| clone the repo and get something running quickly | [docs/quickstarts/FIRST_RUN_5_MIN.md](docs/quickstarts/FIRST_RUN_5_MIN.md) |
| do a full local setup with environment validation | [docs/INSTALL_AND_DEV_QUICKSTART.md](docs/INSTALL_AND_DEV_QUICKSTART.md) |
| understand the repo at a high level | [docs/INDEX.md](docs/INDEX.md) |
| inspect the public E4 dossier configs | [`agent_configs/`](agent_configs/) |
| use the engine from Python or TypeScript | [docs/contracts/cli_bridge/openapi.json](docs/contracts/cli_bridge/openapi.json) and the examples below |
| evaluate parity / replay / evidence | [docs/conformance/README.md](docs/conformance/README.md) |
| integrate BreadBoard into a TS host | [`sdk/ts-backbone/`](sdk/ts-backbone/), [`sdk/ts-host-kits/`](sdk/ts-host-kits/), [`sdk/ts-host-t3/`](sdk/ts-host-t3/) |
| understand claim boundaries before repeating them publicly | [docs/CLAIMS_EVIDENCE_LEDGER.md](docs/CLAIMS_EVIDENCE_LEDGER.md) |

---

## Repository map

```text
breadboard/
├── agent_configs/                 public top-level E4 dossier configs + misc overlays
│   └── misc/                      scenario-specific, historical, and supporting configs
├── agentic_coder_prototype/       canonical Python engine and runtime substrate
│   ├── api/                       CLI bridge server, session runner, protocol surfaces
│   ├── execution/                 runtime execution primitives
│   ├── longrun/                   durable/background execution logic
│   ├── mcp/                       MCP integration layer
│   ├── optimize/                  optimization/evaluation substrate
│   ├── orchestration/             orchestration logic and controllers
│   ├── reward/                    evaluation and reward surfaces
│   ├── rlm/                       recursive language model / long-form work
│   ├── state/                     session/event/transcript state
│   ├── todo/                      task/todo primitives
│   └── tool_calling/              tool routing and related logic
├── breadboard_sdk/                Python SDK
├── sdk/
│   ├── ts/                        CLI-bridge TS SDK
│   ├── ts-backbone/               public TS backbone API
│   ├── ts-workspace/              public workspace / execution-profile layer
│   ├── ts-host-kits/              reusable host-integration abstractions
│   ├── ts-host-bridges/           concrete host bridges (for example OpenClaw)
│   ├── ts-host-t3/                thin-host starter for T3-class apps
│   ├── ts-transport-ai-sdk/       AI SDK projection/session transport adapter
│   ├── ts-kernel-contracts/       TS contract/types/validators for kernel schemas
│   ├── ts-kernel-core/            TS kernel substrate and constrained runtime slices
│   ├── ts-execution-drivers/      shared driver interfaces and planning
│   ├── ts-execution-driver-local/ trusted-local execution driver
│   ├── ts-execution-driver-oci/   OCI / gVisor / Kata-oriented execution driver
│   ├── ts-execution-driver-remote/ delegated remote execution driver
│   └── ts-orchestration-temporal/ durable orchestration adapter work
├── config/
│   ├── e4_targets/                tracked target harness packages
│   ├── longrun/                   long-run and durable-execution config surfaces
│   └── text_contracts/            text and contract artifacts
├── conformance/                   engine fixture bundles and conformance manifests
├── contracts/                     machine-readable kernel and contract schemas
├── docs/                          operator, contract, quickstart, and product docs
├── implementations/               profiles, prompts, tools, and synthesis assets
├── opentui_slab/                  fixed-height OpenTUI-style client
├── scripts/                       setup, validation, replay, export, and maintenance tools
├── tests/                         Python-side validation and conformance tests
├── tool_calling/                  tool-dialect work and examples
├── tools/                         supporting tooling (for example instrumented harness refs)
├── tui_skeleton/                  main terminal client
└── vscode_sidebar/                VSCode sidebar surface
```

If you want the documentation equivalent of that map, see [docs/INDEX.md](docs/INDEX.md).

---

## Install and bootstrap

### Requirements

| Requirement | Recommended | Notes |
|---|---|---|
| Python | 3.11+ | engine, Python SDK, scripts |
| Node.js | 22+ | TUI and TS packages |
| `uv` | recommended | bootstrap prefers `uv` when available |
| `npm` | current stable | used for client and TS package setup |

### Fast path

```bash
bash scripts/dev/bootstrap_first_time.sh
```

Engine-only:

```bash
bash scripts/dev/bootstrap_first_time.sh --profile engine
```

Make shortcuts:

```bash
make setup
make setup-fast
make setup-engine
make setup-fast-engine
```

### Verification

```bash
make doctor
make cli-capabilities
make devx-smoke
make sdk-hello-live
```

### What the bootstrap actually does

- creates or reuses `.venv`
- installs Python dependencies from [`requirements.txt`](requirements.txt)
- builds relevant Node/TS packages when sources are present
- performs doctor/smoke checks unless you explicitly choose a faster path

For the detailed setup reference:

- [docs/INSTALL_AND_DEV_QUICKSTART.md](docs/INSTALL_AND_DEV_QUICKSTART.md)
- [docs/quickstarts/FIRST_RUN_5_MIN.md](docs/quickstarts/FIRST_RUN_5_MIN.md)

---

## Common command paths

| Goal | Command |
|---|---|
| bootstrap everything | `make setup` |
| engine-only bootstrap | `make setup-engine` |
| strict environment doctor | `make doctor` |
| inspect CLI capabilities | `make cli-capabilities` |
| SDK hello verification | `make sdk-hello-live` |
| fast dev-x smoke | `make devx-smoke` |
| full dev-x confidence pass | `make devx-full-pass` |
| validate E4 target manifest | `make e4-target-manifest` |
| validate E4 snapshot coverage | `make e4-snapshot-coverage` |
| strict parity probe | `make e4-postrestore-strict-probe` |
| strict Claude legacy probe | `make e4-claude-legacy-strict-probe` |
| inspect `~/.breadboard` disk usage | `make disk-report` |
| prune `~/.breadboard` safely | `make disk-prune` |

For the full set, see [`Makefile`](Makefile).

---

## Basic usage

### CLI

Run a one-shot task:

```bash
breadboard run \
  --config agent_configs/misc/opencode_mock_c_fs.yaml \
  "Summarize the repository layout in five bullets."
```

Open the TUI:

```bash
breadboard ui --config agent_configs/misc/opencode_mock_c_fs.yaml
```

Run the engine directly from source:

```bash
python -m agentic_coder_prototype.api.cli_bridge.server
```

### Python SDK

```python
from breadboard_sdk import BreadboardClient

client = BreadboardClient(base_url="http://127.0.0.1:9099")
session = client.create_session(
    config_path="agent_configs/misc/opencode_mock_c_fs.yaml",
    task="List the top-level modules and explain each in one line.",
    stream=True,
)

for event in client.stream_events(session["session_id"], query={"schema": 2, "replay": True}):
    if event.get("type") == "assistant_message":
        print(event.get("payload", {}).get("text", ""))
    if event.get("type") == "completion":
        break
```

### TypeScript SDK

```ts
import { createBreadboardClient, streamSessionEvents } from "@breadboard/sdk"

const client = createBreadboardClient({ baseUrl: "http://127.0.0.1:9099" })
const session = await client.createSession({
  config_path: "agent_configs/misc/opencode_mock_c_fs.yaml",
  task: "Explain the purpose of the conformance directory."
})

for await (const event of streamSessionEvents(session.session_id, {
  config: { baseUrl: "http://127.0.0.1:9099" },
})) {
  console.log(event.type)
  if (event.type === "completion") break
}
```

### TypeScript backbone

For serious TS hosts, the public V3 product surface is the Backbone/Workspace/Host Kit layer rather than the raw kernel substrate:

```ts
import { createWorkspace } from "@breadboard/workspace"
import { createBackbone } from "@breadboard/backbone"

const workspace = createWorkspace({
  workspaceId: "demo",
  rootDir: process.cwd(),
})

const backbone = createBackbone({ workspace })
const session = backbone.openSession({
  sessionId: "demo-session",
  requestedModel: "openai/gpt-5.4",
  requestedProvider: "openai",
  projectionProfileId: "ai_sdk_transport",
})
```

### Thin-host starter

For T3/AI-SDK-class hosts:

```ts
import { createT3CodeStarter } from "@breadboard/host-t3"

const starter = createT3CodeStarter()
```

Relevant package docs:

- [sdk/ts-backbone/README.md](sdk/ts-backbone/README.md)
- [sdk/ts-workspace/README.md](sdk/ts-workspace/README.md)
- [sdk/ts-host-kits/README.md](sdk/ts-host-kits/README.md)
- [sdk/ts-host-t3/README.md](sdk/ts-host-t3/README.md)
- [sdk/ts-transport-ai-sdk/README.md](sdk/ts-transport-ai-sdk/README.md)

---

## Public E4 dossiers

One of the strongest features in this repo is that the top-level public E4 configs are no longer tiny opaque overlays. They are intended to read like inspectable harness dossiers.

| Harness | Public dossier | What it gives you |
|---|---|---|
| Codex | [codex_0-107-0_e4_3-6-2026.yaml](agent_configs/codex_0-107-0_e4_3-6-2026.yaml) | current Codex public dossier with rich commentary and tracked target package refs |
| Claude Code | [claude_code_2-1-63_e4_3-6-2026.yaml](agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml) | replay-focused Claude dossier with explicit policy and reminder surfaces |
| OpenCode | [opencode_1-2-17_e4_3-6-2026.yaml](agent_configs/opencode_1-2-17_e4_3-6-2026.yaml) | shared OpenCode dossier with lane-specific replay bundles in `misc/` |
| oh-my-opencode | [oh_my_opencode_3-10-0_e4_3-6-2026.yaml](agent_configs/oh_my_opencode_3-10-0_e4_3-6-2026.yaml) | async/background-task-oriented dossier surface |

Supporting docs:

- [docs/conformance/E4_COOKBOOK_V1.md](docs/conformance/E4_COOKBOOK_V1.md)
- [docs/conformance/E4_TARGET_PACKAGES.md](docs/conformance/E4_TARGET_PACKAGES.md)
- [docs/conformance/E4_DOSSIER_STYLE_GUIDE_V1.md](docs/conformance/E4_DOSSIER_STYLE_GUIDE_V1.md)
- [config/e4_targets/README.md](config/e4_targets/README.md)

> BreadBoard is careful about parity wording. Public dossier readability does **not** mean “everything is magically identical.” Use the evidence docs and replay bundles for exact claim boundaries.

---

## Conformance, replay, and evidence

BreadBoard keeps a deterministic event log and leans heavily on replay/evidence.

### Typical replay workflow

```bash
python scripts/export_cli_bridge_contracts.py
bash scripts/phase12_live_smoke.sh
RUN_DIR="$(ls -1dt logging/* | head -n 1)"
python scripts/log_reduce.py "${RUN_DIR}" --turn-limit 2 --tool-only
```

### Useful entrypoints

| Area | Start here |
|---|---|
| conformance overview | [docs/conformance/README.md](docs/conformance/README.md) |
| test matrix | [docs/conformance/CONFORMANCE_TEST_MATRIX_V1.md](docs/conformance/CONFORMANCE_TEST_MATRIX_V1.md) |
| parity-critical boundaries | [docs/PARITY_KERNEL_BOUNDARIES.md](docs/PARITY_KERNEL_BOUNDARIES.md) |
| public claims and wording guardrails | [docs/CLAIMS_EVIDENCE_LEDGER.md](docs/CLAIMS_EVIDENCE_LEDGER.md) |
| replay-proof quickstart | [docs/quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md](docs/quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md) |
| launch proof media | [docs/media/proof/README.md](docs/media/proof/README.md) |

### What the event log is for

- replay
- evidence extraction
- host projection
- conformance comparisons
- debugging
- future optimization work

The point is not just observability. The point is that the same canonical event truth can feed:

- the TUI
- the VSCode sidebar
- TS host integrations
- replay fixtures
- parity claims

---

## TypeScript package map

| Package | Role |
|---|---|
| [`sdk/ts/`](sdk/ts/) | CLI-bridge TypeScript SDK |
| [`sdk/ts-kernel-contracts/`](sdk/ts-kernel-contracts/) | generated types/validators for kernel contracts |
| [`sdk/ts-kernel-core/`](sdk/ts-kernel-core/) | constrained TS kernel substrate |
| [`sdk/ts-workspace/`](sdk/ts-workspace/) | execution profiles, capability sets, artifact refs, output shaping |
| [`sdk/ts-backbone/`](sdk/ts-backbone/) | public TS backbone API |
| [`sdk/ts-host-kits/`](sdk/ts-host-kits/) | reusable host integration abstractions |
| [`sdk/ts-host-bridges/`](sdk/ts-host-bridges/) | concrete host bridge implementations |
| [`sdk/ts-host-t3/`](sdk/ts-host-t3/) | thin-host starter and migration kits |
| [`sdk/ts-transport-ai-sdk/`](sdk/ts-transport-ai-sdk/) | AI SDK projection/session transport layer |
| [`sdk/ts-execution-drivers/`](sdk/ts-execution-drivers/) | shared execution-driver contracts and helpers |
| [`sdk/ts-execution-driver-local/`](sdk/ts-execution-driver-local/) | trusted-local execution path |
| [`sdk/ts-execution-driver-oci/`](sdk/ts-execution-driver-oci/) | OCI/gVisor/Kata execution path |
| [`sdk/ts-execution-driver-remote/`](sdk/ts-execution-driver-remote/) | delegated remote execution path |
| [`sdk/ts-orchestration-temporal/`](sdk/ts-orchestration-temporal/) | Temporal-oriented orchestration adapter work |

If you are evaluating BreadBoard as a TS backbone rather than as a Python engine with clients, this table is the starting point.

---

## Client surfaces

| Client | Path | Notes |
|---|---|---|
| Main terminal client | [`tui_skeleton/`](tui_skeleton/) | primary TUI surface |
| OpenTUI slab | [`opentui_slab/`](opentui_slab/) | fixed-height slab experiments |
| VSCode sidebar | [`vscode_sidebar/`](vscode_sidebar/) | extension/client surface |
| Python host scripts | [`breadboard_sdk/`](breadboard_sdk/) | Python API consumption |
| TS hosts | [`sdk/ts-backbone/`](sdk/ts-backbone/) and related packages | productized TS integration path |

Useful client docs:

- [docs/TUI_THINKING_STREAMING_CONFIG.md](docs/TUI_THINKING_STREAMING_CONFIG.md)
- [docs/TUI_TODO_EVENT_CONTRACT.md](docs/TUI_TODO_EVENT_CONTRACT.md)
- [docs/VSCODE_SIDEBAR_DOCS_INDEX.md](docs/VSCODE_SIDEBAR_DOCS_INDEX.md)
- [docs/contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md](docs/contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md)
- [docs/contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](docs/contracts/kernel/T3_BACKBONE_ADOPTION_V1.md)
- [docs/contracts/kernel/THIN_HOST_ADOPTION_V1.md](docs/contracts/kernel/THIN_HOST_ADOPTION_V1.md)

---

## Docs navigator

The repo docs are large. These are the most useful entrypoints.

### Setup and onboarding

| Doc | Purpose |
|---|---|
| [docs/quickstarts/FIRST_RUN_5_MIN.md](docs/quickstarts/FIRST_RUN_5_MIN.md) | fastest path from clone to a working local run |
| [docs/INSTALL_AND_DEV_QUICKSTART.md](docs/INSTALL_AND_DEV_QUICKSTART.md) | full setup and environment reference |
| [docs/INDEX.md](docs/INDEX.md) | docs map by audience and task |

### Contracts and kernels

| Doc | Purpose |
|---|---|
| [docs/CONTRACT_SURFACES.md](docs/CONTRACT_SURFACES.md) | stable contract map |
| [docs/PARITY_KERNEL_BOUNDARIES.md](docs/PARITY_KERNEL_BOUNDARIES.md) | byte-stable parity-critical surfaces |
| [docs/contracts/kernel/PROGRAM_INDEX_V1.md](docs/contracts/kernel/PROGRAM_INDEX_V1.md) | kernel and TS backbone program map |

### Conformance and claims

| Doc | Purpose |
|---|---|
| [docs/conformance/README.md](docs/conformance/README.md) | conformance suite overview |
| [docs/CLAIMS_EVIDENCE_LEDGER.md](docs/CLAIMS_EVIDENCE_LEDGER.md) | public claim guardrails |
| [docs/conformance/E4_TARGET_VERSIONING.md](docs/conformance/E4_TARGET_VERSIONING.md) | E4 target freeze/versioning policy |

### TS backbone and adoption

| Doc | Purpose |
|---|---|
| [docs/contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md](docs/contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md) | hard-host adoption path |
| [docs/contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](docs/contracts/kernel/T3_BACKBONE_ADOPTION_V1.md) | thin-host adoption path |
| [docs/contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md](docs/contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md) | current TS-primary-path claim boundary |

---

## Claim boundaries

BreadBoard has strong parity and backbone claims, but it does **not** use vague wording casually.

Two examples of what this README is **not** claiming:

- BreadBoard is not presented here as a blanket drop-in replacement for every other harness.
- BreadBoard is not presented here as having perfect parity everywhere just because dossier configs exist.

Use these before repeating a claim:

- [docs/CLAIMS_EVIDENCE_LEDGER.md](docs/CLAIMS_EVIDENCE_LEDGER.md)
- [docs/PARITY_KERNEL_BOUNDARIES.md](docs/PARITY_KERNEL_BOUNDARIES.md)
- [docs/conformance/README.md](docs/conformance/README.md)

---

## Repo status

| Area | Current posture |
|---|---|
| Engine and CLI bridge | 🟢 active |
| Public E4 dossiers | 🟢 active |
| TS backbone product layer | 🟢 active |
| TUI and client surfaces | 🟢 active / evolving |
| VSCode sidebar | 🟡 active buildout |
| Long-run / RLM / optimization | 🟡 active research/buildout |
| Broad “full replacement” claims for every external harness | 🔴 not implied by this README |

---

## A few repo-specific observations

- The engine-client split is the central design bet, not a detail.
- The public E4 dossiers are meant to be read, not just loaded.
- The TypeScript backbone work is now a real product layer, not only substrate experiments.
- The conformance and evidence story is part of the product, not just internal test infrastructure.

If those things are what you care about, BreadBoard is likely worth your time. If what you want is a minimal wrapper around one provider SDK, it probably is not.

---

## Where to go next

1. Run the quickstart:
   - [docs/quickstarts/FIRST_RUN_5_MIN.md](docs/quickstarts/FIRST_RUN_5_MIN.md)
2. Read the docs map:
   - [docs/INDEX.md](docs/INDEX.md)
3. Open one of the public E4 dossiers:
   - [codex_0-107-0_e4_3-6-2026.yaml](agent_configs/codex_0-107-0_e4_3-6-2026.yaml)
4. If you are a TS host author, start here:
   - [sdk/ts-backbone/README.md](sdk/ts-backbone/README.md)
   - [sdk/ts-host-t3/README.md](sdk/ts-host-t3/README.md)
   - [docs/contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](docs/contracts/kernel/T3_BACKBONE_ADOPTION_V1.md)
5. If you care about proof and parity:
   - [docs/conformance/README.md](docs/conformance/README.md)

