# BreadBoard docs

This is the companion navigator for the root [`README.md`](../README.md).

Use the README when you want the top-level project picture, repository map, package overview, and adoption entrypoints. Use this page when you already know you need documentation and want the fastest path to the right document.

If you are new, start at the row that fits your situation and follow it forward. If you already know what you want, jump directly to the doc.

---

## Start here by role

**Just cloned the repo and want to run something:**
→ [quickstarts/FIRST_RUN_5_MIN.md](quickstarts/FIRST_RUN_5_MIN.md)

**Setting up a local dev environment and need the full reference:**
→ [getting-started/INSTALL_AND_DEV_QUICKSTART.md](getting-started/INSTALL_AND_DEV_QUICKSTART.md)

**Trying to figure out which canonical script family or entrypoint to use:**
→ [guides/OPERATOR_SCRIPT_SURFACE.md](guides/OPERATOR_SCRIPT_SURFACE.md) · [reference/SCRIPTS_INDEX.md](reference/SCRIPTS_INDEX.md)

**Exploring ATP, optimization, DAG, RL, C-Trees, or DARWIN and want the shortest research-systems entry path:**
→ [quickstarts/RESEARCH_SYSTEMS_QUICKSTART.md](quickstarts/RESEARCH_SYSTEMS_QUICKSTART.md) · [concepts/research-systems-overview.md](concepts/research-systems-overview.md)

**Trying to understand the repo’s top-level roots and which ones are canonical:**
→ [reference/REPOSITORY_ZONE_MODEL.md](reference/REPOSITORY_ZONE_MODEL.md)

**Writing code against the engine (Python or TypeScript SDK):**
→ [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json) · SDK examples in the repo README

**Adopting BreadBoard as a TypeScript backbone in a host app:**
→ [contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](contracts/kernel/T3_BACKBONE_ADOPTION_V1.md) · [contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md](contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md) · [contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md](contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md)

**Inspecting the public E4 dossier configs directly:**
→ [`../agent_configs/`](../agent_configs/) · [conformance/E4_TARGET_PACKAGES.md](conformance/E4_TARGET_PACKAGES.md) · [conformance/E4_DOSSIER_STYLE_GUIDE_V1.md](conformance/E4_DOSSIER_STYLE_GUIDE_V1.md)

**Evaluating or reproducing a run (replay/parity/conformance):**
→ [quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md](quickstarts/REPLAY_PROOF_BUNDLE_QUICKSTART.md) · [conformance/README.md](conformance/README.md) · [conformance/E4_TARGET_VERSIONING.md](conformance/E4_TARGET_VERSIONING.md)

**Building or integrating a new client surface (TUI, VSCode, webapp):**
→ [contracts/kernel/PROGRAM_INDEX_V1.md](contracts/kernel/PROGRAM_INDEX_V1.md) · [contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](contracts/kernel/T3_BACKBONE_ADOPTION_V1.md) · [contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md](contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md)

**Publishing a claim, writing a proposal, or making a contract-breaking change:**
→ [contracts/policies/KERNEL_CONTRACT_PACK_V1.md](contracts/policies/KERNEL_CONTRACT_PACK_V1.md) · [contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md](contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md) · [conformance/E4_TARGET_VERSIONING.md](conformance/E4_TARGET_VERSIONING.md)

**Understanding the launch posture, branding, or release gates:**
→ [reference/BRAND.md](reference/BRAND.md) · [ci/RELEASE_LOW_RISK_GATES_20260217.md](ci/RELEASE_LOW_RISK_GATES_20260217.md)

---

## Documentation boundary

Tracked `docs/` is now split intentionally:

- public durable docs live under `getting-started/`, `guides/`, `reference/`, and the existing stable subtrees such as `contracts/`, `conformance/`, `concepts/`, and `media/`
- maintainer-facing tracked material belongs under `internals/`
- preserved tracked historical records belong under `archive/`
- local planning, tranche notes, and research archaeology belong in `docs_tmp/` and are intentionally off the public docs path

---

## Core docs

### Setup and onboarding

| Doc | What it covers |
|-----|----------------|
| [quickstarts/FIRST_RUN_5_MIN.md](quickstarts/FIRST_RUN_5_MIN.md) | Fastest path from clone to a working local setup |
| [quickstarts/RESEARCH_SYSTEMS_QUICKSTART.md](quickstarts/RESEARCH_SYSTEMS_QUICKSTART.md) | Fastest high-quality reading path into ATP, Lean sandboxing, optimization, DAG, RL, C-Trees, and DARWIN |
| [getting-started/INSTALL_AND_DEV_QUICKSTART.md](getting-started/INSTALL_AND_DEV_QUICKSTART.md) | Full setup reference: bootstrap options, doctor, disk maintenance |
| [ci/QUICKSTART_SAFE_VALIDATION_20260217.md](ci/QUICKSTART_SAFE_VALIDATION_20260217.md) | CI-validated quickstart run record |

### Operator and maintainer workflows

| Doc | What it covers |
|-----|----------------|
| [guides/OPERATOR_SCRIPT_SURFACE.md](guides/OPERATOR_SCRIPT_SURFACE.md) | How to choose the right canonical script family and entrypoint for setup, ops, release, migration, and parity work |
| [reference/SCRIPTS_INDEX.md](reference/SCRIPTS_INDEX.md) | Stable categorized script taxonomy and machine-readable inventory entrypoint |

### Contracts and surfaces

| Doc | What it covers |
|-----|----------------|
| [contracts/policies/KERNEL_CONTRACT_PACK_V1.md](contracts/policies/KERNEL_CONTRACT_PACK_V1.md) | Top-level public contract and change-discipline pack |
| [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json) | HTTP + SSE API schema (machine-readable) |
| [conformance/E4_TARGET_VERSIONING.md](conformance/E4_TARGET_VERSIONING.md) | Versioning and freeze doctrine for parity targets |
| [contracts/kernel/PROGRAM_INDEX_V1.md](contracts/kernel/PROGRAM_INDEX_V1.md) | Program-level map of kernel, backbone, and execution surfaces |
| [contracts/kernel/PROGRAM_INDEX_V1.md](contracts/kernel/PROGRAM_INDEX_V1.md) | Kernel, TS backbone, execution-driver, and host-layer program map |

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
| [conformance/E4_COOKBOOK_V1.md](conformance/E4_COOKBOOK_V1.md) | How target harness capture, replay, and dossier refresh works |
| [conformance/E4_TARGET_PACKAGES.md](conformance/E4_TARGET_PACKAGES.md) | Public target-package model behind the E4 dossiers |
| [conformance/E4_DOSSIER_STYLE_GUIDE_V1.md](conformance/E4_DOSSIER_STYLE_GUIDE_V1.md) | How top-level public E4 configs should read and be maintained |
| [reference/SAFE_MODE_EXECUTION_POLICY.md](reference/SAFE_MODE_EXECUTION_POLICY.md) | Execution policy for safe-mode sandboxes |
| [reference/MCP_SERVERS.md](reference/MCP_SERVERS.md) | MCP server inventory and configuration reference |
| [reference/PLUGIN_SECURITY.md](reference/PLUGIN_SECURITY.md) | Plugin trust and security posture |
| [reference/LLM_PROVIDER_DETAILS.md](reference/LLM_PROVIDER_DETAILS.md) | Provider behavior and integration details |
| [reference/SCRIPTS_INDEX.md](reference/SCRIPTS_INDEX.md) | Scripts taxonomy, category meanings, and machine-readable inventory entrypoint |
| [reference/REPOSITORY_ZONE_MODEL.md](reference/REPOSITORY_ZONE_MODEL.md) | Stable meaning of the repo’s top-level roots: product surface, internal runtime substrate, extension space, SDK zones, and support surfaces |
| [contracts/benchmarks/EVIDENCE_V2_CLAIM_LEDGER_V1.md](contracts/benchmarks/EVIDENCE_V2_CLAIM_LEDGER_V1.md) | Evidence v2 claim ledger |

### TypeScript backbone and host adoption

| Doc | What it covers |
|-----|----------------|
| [contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](contracts/kernel/T3_BACKBONE_ADOPTION_V1.md) | Thin-host / T3-style adoption path |
| [contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md](contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md) | Hard-host / OpenClaw-style adoption path |
| [contracts/kernel/THIN_HOST_ADOPTION_V1.md](contracts/kernel/THIN_HOST_ADOPTION_V1.md) | Thin-host migration guidance |
| [contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md](contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md) | Honest support boundary for Python-free primary TS host slices |
| [contracts/kernel/semantics/backbone_api_v1.md](contracts/kernel/semantics/backbone_api_v1.md) | Backbone API semantics |
| [contracts/kernel/semantics/workspace_layer_v1.md](contracts/kernel/semantics/workspace_layer_v1.md) | Workspace layer semantics |
| [contracts/kernel/semantics/host_kit_v1.md](contracts/kernel/semantics/host_kit_v1.md) | Host Kit semantics |
| [contracts/kernel/semantics/ai_sdk_transport_adapter_v1.md](contracts/kernel/semantics/ai_sdk_transport_adapter_v1.md) | AI SDK transport adapter semantics |

### Client surfaces

Client-specific internal buildout notes were intentionally offloaded from the public docs root. Start from the stable adoption and transport docs instead:

| Doc | What it covers |
|-----|----------------|
| [contracts/kernel/T3_BACKBONE_ADOPTION_V1.md](contracts/kernel/T3_BACKBONE_ADOPTION_V1.md) | Thin-host adoption path |
| [contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md](contracts/kernel/OPENCLAW_BACKBONE_ADOPTION_V1.md) | Hard-host adoption path |
| [contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md](contracts/kernel/TS_PRIMARY_HOST_PATH_READINESS_V1.md) | Current TS-primary claim boundary |
| [contracts/cli_bridge/openapi.json](contracts/cli_bridge/openapi.json) | Machine-readable HTTP + SSE bridge contract |

### Research systems and advanced runtime layers

| Doc | What it covers |
|-----|----------------|
| [concepts/research-systems-overview.md](concepts/research-systems-overview.md) | The high-level map for BreadBoard's research-facing subsystems: ATP, Lean sandboxing, optimization, DAG, RL, C-Trees, and DARWIN, including composition boundaries and reading paths |
| [concepts/research-systems-composition-playbook.md](concepts/research-systems-composition-playbook.md) | Practical guide for composing ATP, DAG, optimization, RL, C-Trees, DARWIN, and sandboxing cleanly without letting subsystem boundaries collapse |
| [concepts/research-systems-walkthroughs.md](concepts/research-systems-walkthroughs.md) | Commands-and-files walkthroughs for ATP, optimization, DAG, RL, C-Trees, DARWIN, and their main composition boundaries |
| [concepts/atp-and-lean-sandboxing.md](concepts/atp-and-lean-sandboxing.md) | How ATP contracts and sandbox envelopes fit together, why Lean is a key pressure case, and how formal proving loops stay typed and replayable |
| [concepts/atp-formal-workflows.md](concepts/atp-formal-workflows.md) | Practical ATP workflow guide covering retrieval, decomposition, diagnostics, Hilbert proving slices, and the honest current Lean/sandbox support story |
| [contracts/atp/README.md](contracts/atp/README.md) | Canonical ATP contract-pack entrypoint, current formal-pack runner, adapter slice, and Hilbert comparison surfaces |
| [concepts/optimization-system-overview.md](concepts/optimization-system-overview.md) | A readable synthesis of the optimization stack from substrate through transfer cohorts, promotion, and live experiment-cell doctrine |
| [concepts/optimization-study-playbook.md](concepts/optimization-study-playbook.md) | Practical guide for scoping and running optimization studies cleanly across loci, families, transfer claims, promotion, and live experiment cells |
| [concepts/dag-runtime-system-overview.md](concepts/dag-runtime-system-overview.md) | A readable synthesis of the DAG runtime from search truth through assessments and paper-fidelity helper layers |
| [concepts/dag-paper-replication-playbook.md](concepts/dag-paper-replication-playbook.md) | Practical guide for RSA/PaCoRe-style DAG paper replication with fidelity scorecards, compute ledgers, baseline packets, and deviation tracking |
| [concepts/rl-training-system-overview.md](concepts/rl-training-system-overview.md) | A readable synthesis of the RL overlay from graph-native trajectory truth through fidelity, conformance, and adapter probes |
| [concepts/rl-adapterization-playbook.md](concepts/rl-adapterization-playbook.md) | Practical guide for turning RL export truth into bounded probe-backed adapter paths without letting trainer needs distort BreadBoard truth |
| [concepts/ctrees-system-overview.md](concepts/ctrees-system-overview.md) | Narrative overview of the C-Trees research lane, its tree-shaped control concerns, and its boundaries relative to DAG, RL, and DARWIN |
| [concepts/ctrees-lifecycle-guide.md](concepts/ctrees-lifecycle-guide.md) | Concrete guide to the current C-Trees lifecycle: schema, phase machine, branch receipts, finish closure, helper rehydration, and evaluation-facing bridge layers |
| [concepts/darwin-system-overview.md](concepts/darwin-system-overview.md) | Narrative overview of DARWIN as BreadBoard's outer-loop campaign, lane, policy, evidence, and claim layer |
| [concepts/darwin-campaign-workflow-guide.md](concepts/darwin-campaign-workflow-guide.md) | Practical guide for running DARWIN as a campaign/lane/policy/evidence program rather than a loose pile of stage notes |
| [contracts/darwin/README.md](contracts/darwin/README.md) | Canonical DARWIN contract-pack entrypoint, stage policy docs, and the current Stage-5 plus Stage-6 proving/review script surfaces |

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

### DAG runtime and search

| Doc | What it covers |
|-----|----------------|
| [concepts/dag-runtime-v1-search-surface.md](concepts/dag-runtime-v1-search-surface.md) | DAG Runtime V1 search truth surface, deterministic barriered scheduler, and RSA-style recipe as an opt-in search subsystem |
| [concepts/dag-runtime-v2-assessment-surface.md](concepts/dag-runtime-v2-assessment-surface.md) | DAG Runtime V2 narrow assessment layer: grounded evaluator truth, assessment registry, and explicit linkage into runs, events, and trajectory export |
| [concepts/dag-runtime-v2-e4-widening-packet.md](concepts/dag-runtime-v2-e4-widening-packet.md) | DAG Runtime V2 Phase 3 widening memo showing that verifier, judge/reducer, and branch execute/verify recipes all widen through the same assessment layer |
| [concepts/dag-runtime-v2-optimize-adapter-note.md](concepts/dag-runtime-v2-optimize-adapter-note.md) | DAG Runtime V2 optimize adapter boundary: assessment-grounded DAG artifacts are consumable by optimize without pulling optimize nouns into the DAG kernel |
| [concepts/dag-runtime-v2-darwin-boundary.md](concepts/dag-runtime-v2-darwin-boundary.md) | DAG Runtime V2 DARWIN boundary memo: bounded per-task search stays in DAG while campaign/archive pressure remains outside it |
| [concepts/dag-runtime-v2-rl-facing-note.md](concepts/dag-runtime-v2-rl-facing-note.md) | DAG Runtime V2 RL-facing note: exported assessment-bearing search truth is usable for downstream RL-adjacent studies without turning the runtime into a training framework |
| [concepts/dag-runtime-v3-fidelity-helper-layer.md](concepts/dag-runtime-v3-fidelity-helper-layer.md) | DAG Runtime V3 helper layer: paper recipe manifests, fidelity scorecards, compute ledgers, and deviation tracking on top of a frozen DAG V2 kernel |
| [concepts/dag-runtime-v3-rsa-replication-packet.md](concepts/dag-runtime-v3-rsa-replication-packet.md) | DAG Runtime V3 RSA tranche: fixed-seed `N / K / T` sweeps, budget-matched baselines, and medium-fidelity replication packets without kernel growth |
| [concepts/dag-runtime-v3-pacore-replication-packet.md](concepts/dag-runtime-v3-pacore-replication-packet.md) | DAG Runtime V3 PaCoRe tranche: explicit round profiles, compaction baselines, message-passing ablations, and bounded coding-transfer follow-on without kernel growth |
| [concepts/dag-runtime-v3-cross-paper-composition.md](concepts/dag-runtime-v3-cross-paper-composition.md) | DAG Runtime V3 cross-paper composition: optimize-ready comparison packets, bounded RL-facing export slices, DARWIN boundary updates, and synthesis without kernel growth |

### RL training overlays

| Doc | What it covers |
|-----|----------------|
| [concepts/rl-training-primitives-v1-boundary-and-contract-pack.md](concepts/rl-training-primitives-v1-boundary-and-contract-pack.md) | RL Training Primitives V1 first tranche: overlay-only boundary doctrine, explicit supersession of shallow linear trajectory surfaces, and the initial graph-native contract pack |
| [concepts/rl-training-primitives-v1-replay-parity.md](concepts/rl-training-primitives-v1-replay-parity.md) | RL Training Primitives V1 replay parity: live and replay projection paths must converge on the same graph-core truth surface |
| [concepts/rl-training-primitives-v1-alpha-exporters.md](concepts/rl-training-primitives-v1-alpha-exporters.md) | RL Training Primitives V1 alpha exporters: trainer-neutral export units for SFT, RL transition segments, and verifier examples with explicit provenance and replay parity |
| [concepts/rl-training-primitives-v1-multi-agent-async-hardening.md](concepts/rl-training-primitives-v1-multi-agent-async-hardening.md) | RL Training Primitives V1 multi-agent and async hardening: branch/message/workspace causality, delayed evaluator wake-ups, credit frames, and continuation alignment without kernel growth |
| [concepts/rl-training-primitives-v2-fidelity-hardening.md](concepts/rl-training-primitives-v2-fidelity-hardening.md) | RL Training Primitives V2 first tranche: frozen V1 semantics plus replay/live, compaction, and delayed-evaluation fidelity hardening with export-level helper manifests |
| [concepts/rl-training-primitives-v2-export-conformance.md](concepts/rl-training-primitives-v2-export-conformance.md) | RL Training Primitives V2 Phase 2: canonical export bundles, conformance packets, and replay/live parity views with explicit split, contamination, transform, and fidelity-tier reporting |
| [concepts/rl-training-primitives-v2-adapter-probes.md](concepts/rl-training-primitives-v2-adapter-probes.md) | RL Training Primitives V2 Phase 3: bounded adapter probe reports for serving, evaluator, dataset, and trainer-feedback paths without overclaiming support |
| [concepts/rl-training-primitives-v2-pressure-study-packet.md](concepts/rl-training-primitives-v2-pressure-study-packet.md) | RL Training Primitives V2 Phase 4: representative workload packet and weak-baseline comparisons to prove the graph-native export path matters under budget-matched Mini-default experiments |

### Governance and change policy

| Doc | What it covers |
|-----|----------------|
| [contracts/policies/KERNEL_CONTRACT_PACK_V1.md](contracts/policies/KERNEL_CONTRACT_PACK_V1.md) | Kernel contract governance pack |
| [contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md](contracts/policies/ATP_CONTRACT_BREAK_CHECKLIST_V1.md) | Required steps before breaking a contract surface |
| [contracts/policies/GOVERNANCE_V2_POLICY_V1.md](contracts/policies/GOVERNANCE_V2_POLICY_V1.md) | Governance policy v2 |
| [contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md](contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md) | Rollback protocol |
| [contracts/policies/adr/README.md](contracts/policies/adr/README.md) | Architecture Decision Record index |
| [internals/MIGRATION_AND_WRAPPER_POLICY.md](internals/MIGRATION_AND_WRAPPER_POLICY.md) | Compatibility wrapper lifecycle and migration policy for repo cleanup |
| [internals/research/README.md](internals/research/README.md) | Maintainer-facing research closeout notes, freeze memos, and stop/go syntheses |
| [internals/research/optimization-v6-stop-go-synthesis.md](internals/research/optimization-v6-stop-go-synthesis.md) | Optimization V6 repeated-shape pressure review, no-V7 criteria, and explicit DARWIN handoff signals |
| [internals/research/dag-runtime-v2-phase0-pressure-packet.md](internals/research/dag-runtime-v2-phase0-pressure-packet.md) | DAG Runtime V2 go/no-go packet for the assessment-layer decision |
| [internals/research/dag-runtime-v2-stop-go-synthesis.md](internals/research/dag-runtime-v2-stop-go-synthesis.md) | DAG Runtime V2 stop/go synthesis and freeze criteria |
| [internals/research/dag-runtime-v3-freeze-and-decision-gate.md](internals/research/dag-runtime-v3-freeze-and-decision-gate.md) | DAG Runtime V3 freeze-and-reclassify closeout note |
| [internals/research/rl-training-primitives-v1-freeze-and-deferrals.md](internals/research/rl-training-primitives-v1-freeze-and-deferrals.md) | RL Training Primitives V1 closeout and deferral note |
| [internals/research/rl-training-primitives-v2-freeze-and-deferrals.md](internals/research/rl-training-primitives-v2-freeze-and-deferrals.md) | RL Training Primitives V2 closeout and deferral note |

### Branding and release

| Doc | What it covers |
|-----|----------------|
| [ci/RELEASE_LOW_RISK_GATES_20260217.md](ci/RELEASE_LOW_RISK_GATES_20260217.md) | Low-risk release gates and launch validation posture |
| [reference/BRAND.md](reference/BRAND.md) | Brand guide: colors, logo, voice, claim guardrails |
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
python scripts/release/export_cli_bridge_contracts.py

# Disk maintenance
make disk-report
python scripts/prune_breadboard_home.py --apply
```

---

### Documentation taxonomy

| Doc | What it covers |
|-----|----------------|
| [getting-started/README.md](getting-started/README.md) | First-contact setup, bootstrap, and onboarding path |
| [guides/README.md](guides/README.md) | Task-oriented operator and workflow guidance |
| [reference/README.md](reference/README.md) | Stable factual references, policies, and integration details |
| [internals/README.md](internals/README.md) | Maintainer-facing tracked internal documentation |
| [archive/README.md](archive/README.md) | Durable historical material that remains tracked |

---

## What is not in this index

Operational runbooks, per-phase status docs, and local experiment archaeology live deeper in `docs/contracts/`, `docs/conformance/`, and `docs_tmp/`. They are intentionally not surfaced here to keep this index scannable. The conformance README and the contract/policy surfaces are the right entry points for that depth.
