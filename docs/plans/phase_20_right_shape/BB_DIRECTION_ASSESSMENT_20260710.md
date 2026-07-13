# BreadBoard Direction Assessment — 2026-07-10

Provenance: delivered verbatim in OMP session `019f07ce-1477-7000-a7f0-5713e1bf41f3` continuation, 2026-07-10, by the orchestrating agent after: 11 timeline readers over all 48 session transcript bins, 9 subsystem auditors, direct orchestrator reads of six load-bearing surfaces, 5 adversarial claim-verifiers against raw transcripts and repo, and 3 differently-primed judges (YAGNI / verification-purist / product-pragmatist) briefed with corrected findings. The corrected judge brief is checked in beside this file as `JUDGE_BRIEF_CORRECTED_20260710.md`. Tracking beads: `bb-89y` (assessment, closed), `bb-s5t` (Phase 20 formalization). This document is the reference text for the Phase 20 "Right Shape" campaign (`docs/plans/phase_20_right_shape/BB_RS_MASTER_PLAN.md`).

The assessment below is reproduced VERBATIM from the delivered opinion. Do not edit it; append errata in a dated section at the bottom if corrections are ever needed.

---

# Verdict: right direction, wrong shape — and the shape problem is now the binding constraint

**Method**: 11 timeline readers over all 48 session bins (3.25MB filtered corpus), 9 subsystem auditors, my own direct reads of the six load-bearing surfaces, 5 adversarial claim-verifiers against raw transcripts and repo, then 3 differently-primed judges (YAGNI / verification-purist / product-pragmatist) briefed with the corrected findings. All three independently returned `right_direction_wrong_shape`. Artifacts: `docs_tmp/bb_direction_assessment/`, judge/verifier JSON in session locals. Tracking bead `bb-89y` closed, `bd dolt push` done.

## Was this the right direction? Yes — the thesis is differentiated and partially proven

- The **runtime spine is real**: a 66-line fully-commented `minimal_harness.v2.yaml` boots offline through `v2_loader` → `/v1/sessions` (create/input/commands/files/records/SSE) → typed emission. Exactly **12 schemas are session-time runtime truth** (verified: effective_config_graph, capability_registry, effective_tool_surface, effective_operation_policy, work_item, coordination slice/pack, kernel_event.v2, session_transcript.v2, tool_call/outcome/render.v2).
- The **evidence discipline is the moat**. Exact-scope claims, target pins, C4 chains, semantic comparators, fixed-point regen — no other harness framework makes falsifiable parity claims. It caught real regressions repeatedly (session history proves this ≥6 times in one week).
- **Capture-as-config is closer than my first read suggested** (verifier refuted my overstatement): 17/21 lanes share just **4** registered adapters; 3 lanes are pure data-defined capture; scaffold's builder skeleton is opt-in. Adapter code at the target boundary is inevitable and acceptably shared.

## Where the shape went wrong

1. **"Minimal" stopped being true.** `packs.v1.json` calls 61 kernel + 13 E4 schemas "the minimal expressive harness primitive language" — but only 12 are runtime emissions, 29+10 are merely *validatable*, and the rest are evidence/campaign vocabulary. The catalog absorbed everything the campaigns needed and kept the word "minimal."
2. **Evidence apparatus metastasized into architecture.** A cyclic, replicated hash-pin graph (ledger seed ↔ catalogs ↔ descriptor literal pins ↔ source index) caused ≥6 verified churn episodes in 8 days, each demanding ordered repin/repromote/fixed-point protocols. The 7,292-line P6.6 descriptor mixes author intent with machine-owned pins. And `run_lane.py`'s **normalize/replay/compare stages don't execute** — they're successful-skip metadata. All three judges flagged the vocabulary/behavior gap as the most corrosive single defect.
3. **The product seam is starved — and concretely broken.** Both advertised SDKs (`sdk/ts` client, `breadboard_sdk` Python) call legacy `/sessions`/`/models` routes that default `create_app()` **removes** — the README flow fails against the shipped server. 12/14 TS packages have zero package-name consumers. 62 e4_parity/compilation test modules — including the 733-line `test_e4_api_surface.py` (my "untested router" claim was wrong; "not in CI" is the true one) — are absent from all 24 ci.yml jobs.
4. **The campaign operating system is consuming the project.** Phases 15→19 each redefined "done," and this is the **third time you've asked this same question** — both prior assessments (07-04, 07-06, verified verbatim) concluded right-direction/wrong-shape, and the shape persisted. The constraint isn't knowledge; it's that no freeze or external falsifier ever binds the next campaign.

## What I'd change — ordered by EV

**Now (small):**
1. **Repair the SDK vertical slice**: point both SDKs at `/v1`, add one default-server README-flow smoke to CI. Days of work; unbreaks the only outsider path.
2. **Declare a schema/governance freeze** with explicit unfreeze criteria (a failing demo or consumer case). No new schema families, lane kinds, ledgers, packages, scorecards.
3. **Publish a one-page charter** with the falsifiable thesis: *"an outsider clones harness X in one day: manifest ≤150 lines + adapter ≤300 lines"*. Product = engine + config compiler; E4 = internal conformance backend.
4. **Focused product CI lane** (e4_api_surface, loader/authoring tests, OpenAPI drift, SDK smokes) required on merge; the heavy E4 battery nightly/touched-path. (Judges split on scope; this is the middle that keeps merges honest without making ledger internals merge-critical.)

**Next (medium):**
5. **Consumer-backed contract inventory** → retier the estate: runtime protocol (12) / config algebra / host protocols / evidence / frozen-legacy. Each published schema keeps its tier only with a named consumer. (Note: one Codex lane still rides frozen `lane_def.v1`.)
6. **Thin one lane end-to-end (P6.6) as the manifest/lock pilot**: hand-authored intent (target pin, adapter, assertions, exclusions) compiled into a generated, content-addressed lock. This is the surgical strike on both the 7k-line descriptor and the pin-churn cycle.
7. **One authoring front door**: `breadboard harness init|validate|explain|run` wrapping the existing scripts — no parallel implementation.
8. **Make stage vocabulary honest**: implement normalize/replay/compare as executable plugin contracts or rename the front door to what it does (capture + claim reverify).
9. **Validation consolidation via gap matrix, not blind deletion**: I directly verified hand-rolled re-validation in `helper_runtime_primitives.py`; Judge A reports the same pattern across `v2_loader._validate_v2()`. Before deleting, build the matrix of which Python checks JSON Schema provably covers (and preserve error-message quality), then consolidate. [INFERENCE on v2_loader scope — judge-sourced, not my direct read.]
10. **Flagship "clone a harness in a day" demo** on an unseen target with a predeclared budget — the thesis's first genuine external falsifier.

**Deferred (large, gated on the pilot):**
11. Manifest/lock split across all 21 lanes + content-addressed pins (kills the cyclic hash graph for good).
12. Evidence-core library extraction (Judge B's play) — only after the demo or a second consumer proves reuse demand.

**Anti-recommendations** (explicit, because scope creep is this project's failure mode): no lane CRUD API, no E4 exposure in the public SDK, no TS repackaging campaign, no new campaign scorecards until the demo passes.

## Current utility, plainly

As a **research artifact and conformance system**: genuinely high — the evidence machinery is novel and battle-tested against its own drift. As a **product**: blocked by a small, fixable break (SDK routes) and a large, structural one (authoring/evidence entanglement). The engine you set out to build exists underneath; the next tranche should be the first one measured by an outsider succeeding, not by a ledger converging.

---

# Appendix A — Verified fact base (adversarially tested)

These facts were established by verifiers with file/line evidence and survived (or corrected) adversarial review. Each is the authoritative form; anything conflicting in earlier drafts is superseded.

1. **Runtime-emission schema set (exactly 12 of 93)**: `bb.effective_config_graph.v1`, `bb.capability_registry.v1`, `bb.effective_tool_surface.v1`, `bb.effective_operation_policy.v1`, `bb.work_item.v1`, `bb.coordination_slice.v2`, `bb.coordination_pack.v3`, `bb.kernel_event.v2`, `bb.session_transcript.v2`, `bb.tool_call.v2`, `bb.tool_execution_outcome.v2`, `bb.tool_model_render.v2`. Registered-as-validatable (29 core + 10 E4 in `primitive_records.py:111-159`) ≠ runtime load-bearing. Exact 4-way partition of the remaining ~81 was NOT established.
2. **Capture strategies (21 accepted lanes)**: adapter=17 (sharing 4 registered adapters: `oh_my_pi_compiler_capture`×11, `lane_definition_build`×4 generic, `pi_p5_l1_capture`×1, `pi_p5_l2_capture`×1), replay_dump=2, runtime_records=1, probe_argv=1. Three lanes are data-defined capture (checked-in canonical artifacts; `run_lane.py:207-222` records capture as successful skip). Registry: `contracts/kernel/registries/e4_adapters.v1.json` (6 capture_adapter entries, 2 unused by lanes).
3. **Lane authoring burden is 4-layered**: (a) declaration/scaffold small (~6 fields, `scaffold_e4_target_lane.py`; builder/comparator skeletons opt-in via `--emit-builder-skeleton`/`--emit-comparator-skeleton`, default false); (b) target integration code substantial (adapter/builder Python + probe scripts); (c) capture/evidence work manual (target-observed assertions per `E4_COOKBOOK_V2.md`); (d) promotion/regen machine-maintained (`refresh_lane_descriptor_pins.py`, `regen.py`) but churn-prone.
4. **CI**: 24 jobs, 11 pytest invocations in `.github/workflows/ci.yml`; 57 `tests/e4_parity/` + 5 `tests/compilation/` modules = 62, zero collected. Only E4-adjacent gates: `check_e4_target_freeze_manifest.py` + `tests/test_e4_target_freeze_manifest.py`, and `tests/test_prompt_compilation_golden.py`. `tests/test_e4_api_surface.py` (733-line TestClient suite) exists and is NOT in ci.yml.
5. **SDK**: `tui_skeleton` consumes `@breadboard/kernel-contracts` (`file:../sdk/ts-kernel-contracts`; `tui_skeleton/src/api/types.ts:44` imports `KernelEventV2` from the `./generated` subpath). `scripts/` wire sdk packages by path (`compare_kernel_conformance_engines.py:42-53`, `dev/bootstrap_first_time.sh:339-348`). The other ~12 of 14 packages have no package-name consumers in product trees. **Both advertised SDK clients are broken against default server**: `sdk/ts/src/client.ts`, `sdk/ts/src/stream.ts`, `breadboard_sdk/client.py` call legacy `/sessions`/`/models` removed by `_drop_legacy_routes()` when `BREADBOARD_LEGACY_ROUTES` defaults false.
6. **E4 API**: `agentic_coder_prototype/api/e4/router.py` = 13 read endpoints + POST `/claims/{id}/reverify`; strong integrity checks (catalog binding drift → 409). Real execution API exists separately: cli_bridge `/v1/sessions` (create/list/get, input, commands, attachments, files, records, downloads, SSE), consumed by the TUI.
7. **Stage honesty gap**: `run_lane.py` labels capture/normalize/replay/compare/claim as stages; `_metadata_stage_result()` marks normalize/replay/compare successful-skips; compare defers to the C4 claim command. Vocabulary ≠ behavior (3/3 judges: most corrosive single defect).
8. **Pin-churn history**: ≥6 distinct byte-identity failure episodes 2026-07-03..07-10, caused by a cyclic/highly-replicated hash-pin graph (ledger seed, source index, descriptor literal pins), each requiring ordered refresh/repromote/rebind/fixed-point protocols. Transcript-verified with verbatim quotes.
9. **History pivots**: user requested this same direction assessment on 2026-07-04 and 2026-07-06; both concluded right-direction/wrong-shape equivalents. Agent refused commit backdating on 2026-07-10 and delivered 21 truthfully-dated commits.
10. **Helper/runtime seam (Judge C trace)**: helper-lane YAML does NOT flow through `v2_loader`; among helper compiler entrypoints only `compile_capability_registry` has clear product-runtime consumption (`api/cli_bridge/runtime_emission.py:270-279`); other entrypoints are chiefly parity/test/claim consumers. One Codex lane still uses frozen `bb.e4.lane_def.v1`.
11. **Validation duplication**: directly verified in `compilation/helper_runtime_primitives.py` (`_require_text`/`_require_bool`/... vs JSON Schema). Judge A reports the same pattern across `v2_loader._validate_v2()` (judge-sourced; needs gap matrix before consolidation).
12. **Byte-identity policy nuance (Judge B)**: `E4_COOKBOOK_V2.md` requires byte identity for migrated accepted artifact roles unless a reviewed delta authorizes change, separately from fixed-point regen determinism; comparators are semantic. The "overconstrains" criticism applies to pin replication, not to the comparator layer.

# Appendix B — Judge panel summary (wave 2, briefed)

| Judge | Prior | Verdict | Sharpest correction to the brief | Signature prescription |
|---|---|---|---|---|
| A | YAGNI minimalist | right_direction_wrong_shape | "Router untested" false (`test_e4_api_surface.py`); zero-Python capture too close to success criterion | Freeze E4 growth; tier the schema pack; thin one lane; narrow CI contract; delete duplicated validation; one-page charter |
| B | Verification purist | right_direction_wrong_shape | "~81 evidence-only" classification too broad; cookbook byte-identity policy more precise than claimed | Extract evidence-core library; honest executable stages; manifest→lock compiler; wire full battery into CI sharded |
| C | Product/dev-x pragmatist | right_direction_wrong_shape | "No execution surface" misleading at project level (`/v1/sessions` exists); both SDKs broken vs default server is THE product break | Repair SDK slice; flagship clone-a-harness demo; single CLI front door; freeze 13-package TS graph; HarnessManifest/HarnessLock split |

Convergent across 3/3: manifest/lock split; stop calling 61 schemas "minimal" (tier them); governance freeze until external falsifier; honest stage vocabulary. Split 3 ways on CI scope (narrow product lane vs full battery vs product lane + touched-path battery) — campaign adopts the middle position.
