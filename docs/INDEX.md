# BreadBoard docs

This is the docs map. If you are new, start at the row that fits your situation and follow it forward. If you already know what you want, jump directly to the doc.

---

## Start here by role

**Just cloned the repo and want to run something:**
→ [quickstarts/FIRST_RUN_5_MIN.md](quickstarts/FIRST_RUN_5_MIN.md)

**Setting up a local dev environment and need the full reference:**
→ [INSTALL_AND_DEV_QUICKSTART.md](INSTALL_AND_DEV_QUICKSTART.md)

**Writing code against the engine (Python or TypeScript SDK):**
→ [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json) · SDK examples in the repo README

**Evaluating or reproducing a run (replay/parity/conformance):**
→ [quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md](quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md) · [conformance/README.md](conformance/README.md) · [PARITY_KERNEL_BOUNDARIES.md](PARITY_KERNEL_BOUNDARIES.md)

**Building or integrating a new client surface (TUI, VSCode, webapp):**
→ [VSCODE_SIDEBAR_DOCS_INDEX.md](VSCODE_SIDEBAR_DOCS_INDEX.md) · [TUI_THINKING_STREAMING_CONFIG.md](TUI_THINKING_STREAMING_CONFIG.md) · [CONTRACT_SURFACES.md](CONTRACT_SURFACES.md)

**Publishing a claim, writing a proposal, or making a contract-breaking change:**
→ [CLAIMS_EVIDENCE_LEDGER.md](CLAIMS_EVIDENCE_LEDGER.md) · [contracts/policies/KERNEL_CONTRACT_PACK_V1.md](contracts/policies/KERNEL_CONTRACT_PACK_V1.md) · [contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md](contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md)

**Understanding the launch posture, branding, or release gates:**
→ [RELEASE_LANDING_V1.md](RELEASE_LANDING_V1.md) · [BRAND.md](BRAND.md)

---

## Core docs

### Setup and onboarding

| Doc | What it covers |
|-----|----------------|
| [quickstarts/FIRST_RUN_5_MIN.md](quickstarts/FIRST_RUN_5_MIN.md) | Fastest path from clone to a working local setup |
| [INSTALL_AND_DEV_QUICKSTART.md](INSTALL_AND_DEV_QUICKSTART.md) | Full setup reference: bootstrap options, doctor, disk maintenance |
| [ci/QUICKSTART_SAFE_VALIDATION_20260217.md](ci/QUICKSTART_SAFE_VALIDATION_20260217.md) | CI-validated quickstart run record |

### Contracts and surfaces

| Doc | What it covers |
|-----|----------------|
| [CONTRACT_SURFACES.md](CONTRACT_SURFACES.md) | Top-level map of stable contract boundaries |
| [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json) | HTTP + SSE API schema (machine-readable) |
| [PARITY_KERNEL_BOUNDARIES.md](PARITY_KERNEL_BOUNDARIES.md) | Modules that must stay byte-stable for E4 parity |
| [CLAIMS_EVIDENCE_LEDGER.md](CLAIMS_EVIDENCE_LEDGER.md) | Public claim wording and backing evidence |

### Conformance and testing

| Doc | What it covers |
|-----|----------------|
| [conformance/README.md](conformance/README.md) | Conformance suite overview, runners, artifact contract |
| [conformance/CONFORMANCE_TEST_MATRIX_V1.md](conformance/CONFORMANCE_TEST_MATRIX_V1.md) | CT-* row matrix (source of truth for gate tracking) |
| [conformance/WAVE_A_STATUS.md](conformance/WAVE_A_STATUS.md) | Current Wave A completion status |
| [conformance/GAP_CLOSURE_CRITERIA_V1.md](conformance/GAP_CLOSURE_CRITERIA_V1.md) | Criteria for closing open CT gaps |
| [conformance/E4_TARGET_VERSIONING.md](conformance/E4_TARGET_VERSIONING.md) | E4 parity target freeze and versioning policy |
| [quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md](quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md) | How to run and validate the replay-proof bundle |

### Harness emulation and parity

| Doc | What it covers |
|-----|----------------|
| [PARITY_KERNEL_BOUNDARIES.md](PARITY_KERNEL_BOUNDARIES.md) | Parity-critical module surfaces |
| [SAFE_MODE_EXECUTION_POLICY.md](SAFE_MODE_EXECUTION_POLICY.md) | Execution policy for safe-mode sandboxes |
| [contracts/benchmarks/EVIDENCE_V2_CLAIM_LEDGER_V1.md](contracts/benchmarks/EVIDENCE_V2_CLAIM_LEDGER_V1.md) | Evidence v2 claim ledger |

### Client surfaces

| Doc | What it covers |
|-----|----------------|
| [TUI_THINKING_STREAMING_CONFIG.md](TUI_THINKING_STREAMING_CONFIG.md) | TUI thinking and streaming config options |
| [VSCODE_SIDEBAR_DOCS_INDEX.md](VSCODE_SIDEBAR_DOCS_INDEX.md) | VSCode sidebar docs index |
| [VSCODE_SIDEBAR_QUICKSTART.md](VSCODE_SIDEBAR_QUICKSTART.md) | VSCode sidebar quickstart |
| [VSCODE_SIDEBAR_RPC_CONTRACT_V1.md](VSCODE_SIDEBAR_RPC_CONTRACT_V1.md) | VSCode sidebar RPC contract |
| [VSCODE_SIDEBAR_COMPATIBILITY_MATRIX.md](VSCODE_SIDEBAR_COMPATIBILITY_MATRIX.md) | VSCode sidebar compatibility matrix |
| [VSCODE_SIDEBAR_KNOWN_LIMITS.md](VSCODE_SIDEBAR_KNOWN_LIMITS.md) | Known limits and caveats for the VSCode sidebar |

### Long-run and RLM

| Doc | What it covers |
|-----|----------------|
| [LONGRUN_SAFE_OPERATION.md](LONGRUN_SAFE_OPERATION.md) | Safe operation guide for long-run loops |
| [LONGRUN_POLICY_PACKS.md](LONGRUN_POLICY_PACKS.md) | Policy packs for long-run profiles |
| [LONGRUN_PROFILE_PRESETS.md](LONGRUN_PROFILE_PRESETS.md) | Profile presets reference |
| [LONGRUN_DEBUGGING_GUIDE.md](LONGRUN_DEBUGGING_GUIDE.md) | Debugging long-run sessions |
| [RLM_MODE_QUICKSTART.md](RLM_MODE_QUICKSTART.md) | RLM mode quickstart |
| [RLM_PHASE2_EVAL_RUNBOOK.md](RLM_PHASE2_EVAL_RUNBOOK.md) | Phase 2 eval runbook |

### Optimization

| Doc | What it covers |
|-----|----------------|
| [concepts/optimization-v1-substrate.md](concepts/optimization-v1-substrate.md) | Optimization V1 substrate primitives, bounded overlays, and the canonical codex dossier example |
| [concepts/optimization-v1-datasets.md](concepts/optimization-v1-datasets.md) | Optimization V1 dataset, ground-truth, and correctness-rationale records with anti-slop guidance |
| [concepts/optimization-v1-evaluation.md](concepts/optimization-v1-evaluation.md) | Optimization V1 evaluation records, diagnostics, wrongness taxonomy, and the canonical evaluation example |
| [concepts/optimization-v1-backend.md](concepts/optimization-v1-backend.md) | Optimization V1 reflective backend, objective vector, bounded mutation policy, and non-dominated portfolio retention |
| [concepts/optimization-v1-promotion.md](concepts/optimization-v1-promotion.md) | Optimization V1 promotion state machine, replay/conformance gates, support-envelope gating, and canonical promotion outcomes |
| [concepts/optimization-v1-runtime-context.md](concepts/optimization-v1-runtime-context.md) | Optimization V1 environment-aware sample model, tool-pack-aware optimization inputs, runtime compatibility checks, and DARWIN boundary |
| [concepts/optimization-v1-5-evidence-integration.md](concepts/optimization-v1-5-evidence-integration.md) | Optimization V1.5 benchmark manifests, candidate comparison results, benchmark run results, and the first support/execution evidence slice |
| [concepts/optimization-v2-suites-and-target-families.md](concepts/optimization-v2-suites-and-target-families.md) | Optimization V2 evaluation suites, objective suites, target families, bounded search spaces, and the first live support/execution family binding |
| [concepts/optimization-v3-composed-families-and-search-policy.md](concepts/optimization-v3-composed-families-and-search-policy.md) | Optimization V3 family composition, composition-aware search-space constraints, reward-boundary doctrine, and Nano/Mini cost policy |

### Governance and change policy

| Doc | What it covers |
|-----|----------------|
| [contracts/policies/KERNEL_CONTRACT_PACK_V1.md](contracts/policies/KERNEL_CONTRACT_PACK_V1.md) | Kernel contract governance pack |
| [contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md](contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md) | Required steps before breaking a contract surface |
| [contracts/policies/GOVERNANCE_V2_POLICY_V1.md](contracts/policies/GOVERNANCE_V2_POLICY_V1.md) | Governance policy v2 |
| [contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md](contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md) | Rollback protocol |
| [contracts/policies/adr/README.md](contracts/policies/adr/README.md) | Architecture Decision Record index |

### Branding and release

| Doc | What it covers |
|-----|----------------|
| [RELEASE_LANDING_V1.md](RELEASE_LANDING_V1.md) | Release entrypoint and readiness checklist |
| [BRAND.md](BRAND.md) | Brand guide: colors, logo, voice, claim guardrails |
| [media/branding/README.md](media/branding/README.md) | Canonical branding asset hashes and lock record |
| [media/branding/WEB_EMBED_SNIPPETS.md](media/branding/WEB_EMBED_SNIPPETS.md) | Ready-to-use web embed snippets |
| [media/proof/README.md](media/proof/README.md) | Launch proof media bundle |

---

## Key scripts (quick reference)

```bash
# Setup
bash scripts/dev/bootstrap_first_time.sh
make setup-fast

# Doctor
python scripts/dev/first_time_doctor.py --strict
make doctor

# Conformance suite
scripts/run_wave_a_conformance_bundle.sh artifacts/conformance
python scripts/run_conformance_matrix.py
python scripts/run_ct_scenarios.py --fail-on-unimplemented-all

# Parity / replay
python scripts/run_parity_replays.py
python scripts/export_cli_bridge_contracts.py

# Disk maintenance
make disk-report
python scripts/prune_breadboard_home.py --apply
```

---

## What is not in this index

Operational runbooks, per-phase status docs, and experiment manifests live deeper in `docs/contracts/`, `docs/conformance/`, and `docs_tmp/`. They are intentionally not surfaced here to keep this index scannable. The conformance README and the contract surfaces doc are the right entry points for that depth.
