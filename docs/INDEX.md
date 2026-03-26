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
| [concepts/optimization-v4-mixed-evidence-package-lanes.md](concepts/optimization-v4-mixed-evidence-package-lanes.md) | Optimization V4 transfer slices, mixed-evidence suite extensions, package-scoped triplet lanes, and the reward-boundary doctrine after V3 |
| [concepts/optimization-v5-transfer-cohorts-and-generalization-evidence.md](concepts/optimization-v5-transfer-cohorts-and-generalization-evidence.md) | Optimization V5 transfer cohorts, claim tiers, Codex↔OpenCode cohort methodology, and Nano-first generalization doctrine |
| [concepts/optimization-v6-live-experiment-cells-and-kernel-freeze.md](concepts/optimization-v6-live-experiment-cells-and-kernel-freeze.md) | Optimization V6 public-kernel freeze, live cohort-cell doctrine, Nano/Mini fairness rules, and the explicit optimize ↔ reward boundary under repeated experiments |
| [concepts/optimization-v6-stop-go-synthesis.md](concepts/optimization-v6-stop-go-synthesis.md) | Optimization V6 synthesis note: repeated-shape pressure review, no-V7 criteria, and explicit DARWIN handoff signals |
| [concepts/dag-runtime-v1-search-surface.md](concepts/dag-runtime-v1-search-surface.md) | DAG Runtime V1 search truth surface, deterministic barriered scheduler, and RSA-style recipe as an opt-in search subsystem |
| [concepts/dag-runtime-v2-phase0-pressure-packet.md](concepts/dag-runtime-v2-phase0-pressure-packet.md) | DAG Runtime V2 Phase 0 go/no-go packet showing repeated assessment-truth pressure across verifier, judge/reducer, and branch execute/verify cells |
| [concepts/dag-runtime-v2-assessment-surface.md](concepts/dag-runtime-v2-assessment-surface.md) | DAG Runtime V2 narrow assessment layer: grounded evaluator truth, assessment registry, and explicit linkage into runs, events, and trajectory export |
| [concepts/dag-runtime-v2-e4-widening-packet.md](concepts/dag-runtime-v2-e4-widening-packet.md) | DAG Runtime V2 Phase 3 widening memo showing that verifier, judge/reducer, and branch execute/verify recipes all widen through the same assessment layer |
| [concepts/dag-runtime-v2-optimize-adapter-note.md](concepts/dag-runtime-v2-optimize-adapter-note.md) | DAG Runtime V2 optimize adapter boundary: assessment-grounded DAG artifacts are consumable by optimize without pulling optimize nouns into the DAG kernel |
| [concepts/dag-runtime-v2-darwin-boundary.md](concepts/dag-runtime-v2-darwin-boundary.md) | DAG Runtime V2 DARWIN boundary memo: bounded per-task search stays in DAG while campaign/archive pressure remains outside it |
| [concepts/dag-runtime-v2-rl-facing-note.md](concepts/dag-runtime-v2-rl-facing-note.md) | DAG Runtime V2 RL-facing note: exported assessment-bearing search truth is usable for downstream RL-adjacent studies without turning the runtime into a training framework |
| [concepts/dag-runtime-v2-stop-go-synthesis.md](concepts/dag-runtime-v2-stop-go-synthesis.md) | DAG Runtime V2 stop/go synthesis: V2 stops with the assessment layer and should stay frozen unless repeated evidence forces another public shape |
| [concepts/dag-runtime-v3-fidelity-helper-layer.md](concepts/dag-runtime-v3-fidelity-helper-layer.md) | DAG Runtime V3 helper layer: paper recipe manifests, fidelity scorecards, compute ledgers, and deviation tracking on top of a frozen DAG V2 kernel |
| [concepts/dag-runtime-v3-rsa-replication-packet.md](concepts/dag-runtime-v3-rsa-replication-packet.md) | DAG Runtime V3 RSA tranche: fixed-seed `N / K / T` sweeps, budget-matched baselines, and medium-fidelity replication packets without kernel growth |
| [concepts/dag-runtime-v3-pacore-replication-packet.md](concepts/dag-runtime-v3-pacore-replication-packet.md) | DAG Runtime V3 PaCoRe tranche: explicit round profiles, compaction baselines, message-passing ablations, and bounded coding-transfer follow-on without kernel growth |
| [concepts/dag-runtime-v3-cross-paper-composition.md](concepts/dag-runtime-v3-cross-paper-composition.md) | DAG Runtime V3 cross-paper composition: optimize-ready comparison packets, bounded RL-facing export slices, DARWIN boundary updates, and synthesis without kernel growth |
| [concepts/dag-runtime-v3-freeze-and-decision-gate.md](concepts/dag-runtime-v3-freeze-and-decision-gate.md) | DAG Runtime V3 closeout: freeze-and-reclassify decision gate that keeps the kernel frozen and pushes remaining pressure to helper, optimize, RL, evaluator, and DARWIN-adjacent layers |
| [concepts/rl-training-primitives-v1-boundary-and-contract-pack.md](concepts/rl-training-primitives-v1-boundary-and-contract-pack.md) | RL Training Primitives V1 first tranche: overlay-only boundary doctrine, explicit supersession of shallow linear trajectory surfaces, and the initial graph-native contract pack |
| [concepts/rl-training-primitives-v1-replay-parity.md](concepts/rl-training-primitives-v1-replay-parity.md) | RL Training Primitives V1 replay parity: live and replay projection paths must converge on the same graph-core truth surface |
| [concepts/rl-training-primitives-v1-alpha-exporters.md](concepts/rl-training-primitives-v1-alpha-exporters.md) | RL Training Primitives V1 alpha exporters: trainer-neutral export units for SFT, RL transition segments, and verifier examples with explicit provenance and replay parity |
| [concepts/rl-training-primitives-v1-multi-agent-async-hardening.md](concepts/rl-training-primitives-v1-multi-agent-async-hardening.md) | RL Training Primitives V1 multi-agent and async hardening: branch/message/workspace causality, delayed evaluator wake-ups, credit frames, and continuation alignment without kernel growth |
| [concepts/rl-training-primitives-v1-freeze-and-deferrals.md](concepts/rl-training-primitives-v1-freeze-and-deferrals.md) | RL Training Primitives V1 closeout: delegated-boundary probes, optional training feedback support, and the V1 freeze/defer decision |
| [concepts/rl-training-primitives-v2-fidelity-hardening.md](concepts/rl-training-primitives-v2-fidelity-hardening.md) | RL Training Primitives V2 first tranche: frozen V1 semantics plus replay/live, compaction, and delayed-evaluation fidelity hardening with export-level helper manifests |
| [concepts/rl-training-primitives-v2-export-conformance.md](concepts/rl-training-primitives-v2-export-conformance.md) | RL Training Primitives V2 Phase 2: canonical export bundles, conformance packets, and replay/live parity views with explicit split, contamination, transform, and fidelity-tier reporting |
| [concepts/rl-training-primitives-v2-adapter-probes.md](concepts/rl-training-primitives-v2-adapter-probes.md) | RL Training Primitives V2 Phase 3: bounded adapter probe reports for serving, evaluator, dataset, and trainer-feedback paths without overclaiming support |
| [concepts/rl-training-primitives-v2-pressure-study-packet.md](concepts/rl-training-primitives-v2-pressure-study-packet.md) | RL Training Primitives V2 Phase 4: representative workload packet and weak-baseline comparisons to prove the graph-native export path matters under budget-matched Mini-default experiments |
| [concepts/rl-training-primitives-v2-freeze-and-deferrals.md](concepts/rl-training-primitives-v2-freeze-and-deferrals.md) | RL Training Primitives V2 closeout: freeze the proof-led helper surfaces and explicitly defer anything not forced by repeated pressure |

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
