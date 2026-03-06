# BreadBoard release landing

Single entrypoint for launch-facing technical onboarding. Start here if you are evaluating the project for the first time.

---

## What BreadBoard is

BreadBoard is a contract-backed agent engine and multi-client coding stack. The engine runs as a FastAPI service and emits a canonical SSE event stream. Every client—TUI, Python SDK, TypeScript SDK, VSCode sidebar, or your own code—consumes that stream over a stable HTTP + SSE surface.

Every run produces an append-only event log that is portable, deterministically ordered, and replayable. That log is not a debugging convenience; it is the primary artifact.

---

## Fast start

For full prerequisites and local workflow:

→ [INSTALL_AND_DEV_QUICKSTART.md](INSTALL_AND_DEV_QUICKSTART.md)

Minimal commands:

```bash
npm -C tui_skeleton ci
npm -C tui_skeleton run build
breadboard doctor --config agent_configs/misc/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/misc/opencode_mock_c_fs.yaml "Say hi and exit."
```

5-minute copy-paste path:

→ [quickstarts/FIRST_RUN_5_MIN.md](quickstarts/FIRST_RUN_5_MIN.md)

---

## Proof path (artifact-backed)

When you need reproducible evidence rather than narrative claims:

→ [quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md](quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md)
→ [media/proof/README.md](media/proof/README.md)

Core commands:

```bash
python scripts/export_cli_bridge_contracts.py
bash scripts/phase12_live_smoke.sh
RUN_DIR="$(ls -1dt logging/* | head -n 1)"
python scripts/log_reduce.py "${RUN_DIR}" --turn-limit 2 --tool-only > docs/media/proof/launch_v1/log_reduce_sample_v1.txt
```

---

## Contract surfaces

Stable boundaries and change policy:

→ [CONTRACT_SURFACES.md](CONTRACT_SURFACES.md)
→ [CLI_BRIDGE_PROTOCOL_VERSIONING.md](CLI_BRIDGE_PROTOCOL_VERSIONING.md)
→ [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json)

---

## Claim discipline

All public wording must be backed by evidence:

→ [CLAIMS_EVIDENCE_LEDGER.md](CLAIMS_EVIDENCE_LEDGER.md)

Two things this project does not claim without evidence:

- "drop-in replacement" for other harnesses
- "perfect parity"

Coverage is defined in [PARITY_KERNEL_BOUNDARIES.md](PARITY_KERNEL_BOUNDARIES.md) and the conformance matrix.

---

## Branding and media

→ [BRAND.md](BRAND.md)
→ [media/branding/README.md](media/branding/README.md)
→ [media/branding/WEB_EMBED_SNIPPETS.md](media/branding/WEB_EMBED_SNIPPETS.md)
→ [media/proof/README.md](media/proof/README.md)

---

## What to read next, by situation

| Situation | Start here |
|-----------|------------|
| Just cloned, want to run something | [quickstarts/FIRST_RUN_5_MIN.md](quickstarts/FIRST_RUN_5_MIN.md) |
| Setting up a full dev environment | [INSTALL_AND_DEV_QUICKSTART.md](INSTALL_AND_DEV_QUICKSTART.md) |
| Writing code against the engine | [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json) |
| Evaluating replay or parity | [quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md](quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md) |
| Building or integrating a client | [CONTRACT_SURFACES.md](CONTRACT_SURFACES.md) · [VSCODE_SIDEBAR_DOCS_INDEX.md](VSCODE_SIDEBAR_DOCS_INDEX.md) |
| Checking public claims | [CLAIMS_EVIDENCE_LEDGER.md](CLAIMS_EVIDENCE_LEDGER.md) |
| Full docs map | [INDEX.md](INDEX.md) |

---

## Release readiness checklist (v1)

- [x] Quickstart commands pass
- [x] Proof bundle commands pass
- [x] Contract export is current
- [x] Claims ledger wording matches current evidence
- [x] Branding assets and hash list are current
- [x] Deterministic launch proof media bundle exists in-repo
- [x] Staging gates reviewed before broad launch posting

Latest quickstart validation record:

- [ci/QUICKSTART_SAFE_VALIDATION_20260217.md](ci/QUICKSTART_SAFE_VALIDATION_20260217.md)
- [ci/RELEASE_LOW_RISK_GATES_20260217.md](ci/RELEASE_LOW_RISK_GATES_20260217.md)
- [ci/LAUNCH_STAGING_REVIEW_20260217.md](ci/LAUNCH_STAGING_REVIEW_20260217.md)
- [ci/CHANNEL_FEEDBACK_INCORPORATION_CHECKLIST_V1.md](ci/CHANNEL_FEEDBACK_INCORPORATION_CHECKLIST_V1.md)

---

## Launch staging status

Current policy: repo/docs/proof hardening before broad external posting. HN and similar channel postings are deferred until staging gates are satisfied.

Staging sequence and gates: [LAUNCH_STAGING_PLAN_V1.md](LAUNCH_STAGING_PLAN_V1.md)
