# BreadBoard Release Landing (v1)

This is the single entrypoint for launch-facing technical onboarding.

## 1) Category and Promise

BreadBoard is the **agent harness kernel**:

- contract-backed event/stream surfaces,
- replayable run artifacts,
- projection-driven clients (TUI/SDK) over stable boundaries.

## 2) Fast Start

For full prerequisites and local workflow:

- `docs/INSTALL_AND_DEV_QUICKSTART.md`

Minimal commands:

```bash
npm -C tui_skeleton ci
npm -C tui_skeleton run build
breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/opencode_mock_c_fs.yaml "Say hi and exit."
```

## 3) Proof Path (Artifact-Backed)

Use this when you need reproducible evidence instead of narrative claims:

- `docs/quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md`
- `docs/media/proof/README.md`

Core commands:

```bash
python scripts/export_cli_bridge_contracts.py
bash scripts/phase12_live_smoke.sh
RUN_DIR="$(ls -1dt logging/* | head -n 1)"
python scripts/log_reduce.py "${RUN_DIR}" --turn-limit 2 --tool-only > docs/media/proof/launch_v1/log_reduce_sample_v1.txt
```

## 4) Contract Surfaces

Start here for stable boundaries and change policy:

- `docs/CONTRACT_SURFACES.md`
- `docs/CLI_BRIDGE_PROTOCOL_VERSIONING.md`
- `docs/contracts/cli_bridge/openapi.json`

## 5) Claim Discipline

Public wording must be tied to evidence:

- `docs/CLAIMS_EVIDENCE_LEDGER.md`

## 6) Branding and Media

Canonical branding assets and embed snippets:

- `docs/BRAND.md`
- `docs/media/branding/README.md`
- `docs/media/branding/WEB_EMBED_SNIPPETS.md`
- `docs/media/proof/README.md`

## 7) Launch Staging Status

Launch sequencing and gates:

- `docs/LAUNCH_STAGING_PLAN_V1.md`

Current policy:

- repo/docs/proof hardening first,
- early channel validation first,
- Hacker News posting deferred until staging gates are satisfied.

## 8) "What to Read Next" by Persona

Solo builder:

- `docs/INSTALL_AND_DEV_QUICKSTART.md`
- `docs/quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md`

Infra/platform engineer:

- `docs/CONTRACT_SURFACES.md`
- `docs/CLAIMS_EVIDENCE_LEDGER.md`

TUI/operator workflow maintainer:

- `docs/TUI_TODO_EVENT_CONTRACT.md`
- `docs/TUI_THINKING_STREAMING_CONFIG.md`

## 9) Release Readiness Checklist (v1)

- [x] Quickstart commands still pass.
- [x] Proof bundle commands still pass.
- [x] Contract export is current.
- [x] Claims ledger wording matches current evidence.
- [x] Branding assets/hash list are current.
- [x] Deterministic launch proof media bundle exists in-repo.
- [x] Staging gates reviewed before broad launch posting.

Latest quickstart validation record:

- `docs/ci/QUICKSTART_SAFE_VALIDATION_20260217.md`
- `docs/ci/RELEASE_LOW_RISK_GATES_20260217.md`
- `docs/ci/LAUNCH_STAGING_REVIEW_20260217.md`
- `docs/ci/CHANNEL_FEEDBACK_INCORPORATION_CHECKLIST_V1.md`
