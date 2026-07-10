# Phase 20 Spec Amendments

Every deviation from BB_RS_MASTER_PLAN.md is recorded here, dated, with evidence (§1.5 spec_gap protocol).

Current state: **17 amendments** (below).

---

## Amendment 1 — 2026-07-10 — Shared immutable interpreter for packet worktrees (spec_gap)

**Rule amended:** §1.1 Interpreter rule.
**Gap:** the rule resolves `PYTHON="$REPO_ROOT/.venv/bin/python"` per packet; packet isolation (§1.2) executes packets in git worktrees, which have no `.venv`. Bootstrapping a full venv per worktree duplicates an identical environment 6+ times per wave for zero isolation benefit (gates never mutate the env).
**Amendment:** a packet worktree MAY use the integration checkout's interpreter (`/Users/kylemccleary/projects/breadboard/breadboard_repo_integration_main_20260326/.venv/bin/python`) as `$PYTHON` iff (1) `requirements.txt` at the packet's base SHA is byte-identical to the integration checkout's — recorded digest `sha256:9f19031842d0d2c2d51e39aa3deda5500ec70c530b872890aaaed0883eebd65f`; (2) no gate mutates the environment (any `pip install` requires a fresh env + explicit evidence); (3) packet evidence records the interpreter path + this digest. Gates that REQUIRE a fresh environment (G1 fresh-venv editable-install test, G6 temp-checkout E2E) still bootstrap their own per the original rule.
**Interpreter:** Python 3.11.15 at `/Users/kylemccleary/projects/breadboard/breadboard_repo_integration_main_20260326/.venv/bin/python`.
**Recorded by:** orchestrating agent, session bootstrap, branch e4/workspace-restore-20260708 @ 3b8d862f.

---

## Amendment 2 — 2026-07-10 — G1 fresh-venv bootstrap requires pip>=21.3 (gate_wrong)

**Gate amended:** WS-G G1 ACCEPT (fresh-temp-venv editable-install test) and, by extension, any fresh-venv bootstrap (G6, §1.1 interpreter-rule bootstrap).
**Evidence of failure:** macOS system `python3 -m venv` ships pip 21.2.4 / setuptools 58.0.4 (implementer WsGCliSkeleton, 2026-07-10); `pip install -e <root>` on a pyproject-only project requires PEP 660 editable support, added in pip 21.3 (Oct 2021). Exact failure: pip 21.2.4 rejects editable install without setup.py.
**Amendment:** the fresh-venv bootstrap procedure is normatively `python3 -m venv <V> && <V>/bin/pip install --upgrade 'pip>=21.3' && <V>/bin/pip install -e <repo-root>`. Minimum pip is pinned explicitly in the command (not a comment). Rejected alternative: a legacy `setup.py` shim (packaging-surface creep against the G1 "no packaging campaign" constraint).
**Classification:** gate_wrong (the gate's written bootstrap could not succeed in the target environment).
**Recorded by:** orchestrating agent, before accepting any G1 gate transcript.

---

## Amendment 3 — 2026-07-10 — B2 "wire into product-spine" clause ownership (spec_gap)

**Ambiguity:** B2's item text contains "Wire into product-spine CI job (WS-D)" while B2's ACCEPT gate covers only the script + negative tests. Two defensible readings: (a) B2 is not done until ci.yml wires the check; (b) the "(WS-D)" tag delegates wiring to D1, whose command list already mandates `python scripts/check_phase20_freeze.py` in product-spine.
**Resolution:** reading (b). B2's ACCEPT is its gate (plan-wide convention: ACCEPT defines done). The wiring obligation is OWNED by D1: D1's acceptance is amended to explicitly include "ci.yml product-spine job runs scripts/check_phase20_freeze.py, and its verifier confirms this step exists and passes." The WS-B child issue stays open until D1 lands (bd closure rule §1.6 already requires all-workstream-items done; B3 is also pending).
**Effect on scoring:** B2's 20 points stand (gate met at verified head de76cf2b); no points attach to the wiring twice; D1 cannot pass without the wiring.
**Classification:** spec_gap.

---

## Amendment 4 - 2026-07-10 - AM2 bootstrap must install requirements before editable package (gate_wrong)

AM2's fresh-venv bootstrap (`venv -> upgrade pip -> pip install -e <root>`) is incomplete: the root `pyproject.toml` (G1) intentionally declares **no dependencies**, and pre-AM2 bootstrap provisioned `requirements.txt`. A fresh checkout following AM2 verbatim gets an editable package with none of its runtime/test dependencies.

Corrected normative bootstrap (supersedes AM2's command only; AM2's pip>=21.3 rationale stands):

    python3 -m venv "<V>" \
      && "<V>/bin/python" -m pip install -U pip \
      && "<V>/bin/python" -m pip install -r "<repo-root>/requirements.txt" \
      && "<V>/bin/python" -m pip install -e "<repo-root>"

All pip invocations use `python -m pip`. G6's fresh-checkout gate MUST execute this exact sequence. G1's existing verification remains valid (its gate exercised editable-install mechanics only; classification: gate_wrong in AM2, not a G1 defect).

---

## Amendment 5 - 2026-07-10 - three spec gaps surfaced by H1 (spec_gap; owners assigned)

H1's honest-execution battery exposed plan-vs-repo mismatches. None are H1 defects; H1's fail-closed reds are the intended signal. Owners:

1. **/replay/mode absent from all 21 normalized legacy lanes** (only /replay/session + /replay/comparator_class exist; session null). Rule 2's `replay.mode == "stored"` check cannot pass on any legacy lane until **H3** adds `mode` plus concrete stored-artifact refs. Until then, replay-reuse reds are expected and correct.
2. **Undeclared lane kind**: legacy /kind values are `target_support` (12) and `non_target_accounting` (9); STAGES_BY_KIND declares `target_support`, `self_runtime`, `probe`. **WS-F** must either migrate the kind or amend the table with an authoritative tuple. Interim rule: H-packets treat `non_target_accounting` as legacy-undeclared and fail closed.
3. **Directory reused-inputs lack digest semantics**: e.g. `config/e4_targets/claude_code/2.1.63` is a directory at /capture/inputs. **H3** must declare concrete stored artifact files or define canonical directory-tree digest semantics before stored-capture reuse can pass.

---

## Amendment 6 - 2026-07-10 - A7 records step: minimal SDK widening authorized (spec_gap)

A7's item text requires the demo flow create -> send-input -> read-records -> stream-events "through BOTH SDKs", but the S1/§4.6 repair map contains no records operation and forbids widening. The canonical contract `docs/contracts/cli_bridge/openapi.json` DOES expose `GET /v1/sessions/{session_id}/records` (operationId `get_session_records_v1_sessions__session_id__records_get`; served by create_app at app.py:668-684), so this is a plan-internal conflict, not a missing server surface.

Resolution: the §4.6 repair map is amended with EXACTLY ONE additional operation per SDK, mapped to that route:
- TS `sdk/ts` client: `readSessionRecords(sessionId)` -> `GET /v1/sessions/${sessionId}/records` (naming follows `readSessionFile`).
- Python `breadboard_sdk/client.py`: `read_session_records(session_id)` -> same route.

No other widening is authorized. A7's ACCEPT stands as written (records through both SDKs). Freeze-baseline implications: none (no new package identity, schema ID, lane, or governance file; B2 inventories unaffected). Raw-HTTP substitution for the records leg is expressly rejected.

---

## Amendment 7 - 2026-07-10 - F1 manifest digest enforcement and legacy lane-kind table (spec_gap)

Two declaration gaps are resolved together because F1 introduces the manifest kind contract consumed by the H-stage table:

1. **Manifest digest guards.** Section 4.1 says that per-field schema patterns guard against `sha256:`, but the authoritative verbatim schema contains no such patterns. The verbatim schema remains unchanged. `scripts/authoring/validate_lane.py` is the normative enforcement layer and MUST reject the literal `sha256:` substring anywhere in the source document before parsing or schema validation. This whole-document check covers arbitrary nested values and is stronger than an incomplete list of field patterns.
2. **Legacy `non_target_accounting` kind.** Amendment 5 gap 2 is resolved without relabeling the 9 existing lane sources and without widening the new manifest schema. `non_target_accounting` remains a legacy lane-def-only kind. Its authoritative `STAGES_BY_KIND` tuple is `("capture", "normalize", "replay", "compare", "claim")`, matching all five declared sections present in each of those 9 sources. The next WS-H packet (H2) MUST add this row to the table with a covering test; until that lands, `non_target_accounting` continues to fail closed per Amendment 5's interim rule. New `bb.e4.lane_manifest.v1` documents remain limited to `target_support`, `self_runtime`, and `probe` exactly as section 4.1 specifies.

**Classification:** spec_gap.
**Owner:** WS-F F1 (enforcement layer); H2 (table row + test).
**Recorded-by:** orchestrator.

---

## Registry normalization - 2026-07-10 - explicit fields for AM1, AM2, AM4, AM6 (bookkeeping; no normative change)

Wave-2 derailment audit found four amendments lacking explicit owner/classification fields in this prose registry (the machine ledger already carried them). For the record:

- **AM1** (shared immutable interpreter): Classification: spec_gap. Owner: campaign-wide. Recorded-by: orchestrator.
- **AM2** (G1 fresh-venv bootstrap pip>=21.3): Classification: gate_wrong. Owner: G-items. Recorded-by: orchestrator.
- **AM4** (bootstrap installs requirements.txt before editable package): Classification: gate_wrong (AM4 itself; the parenthetical in its body classifies the AM2 defect it corrects). Owner: G6 gate + any fresh-venv gate. Recorded-by: orchestrator.
- **AM6** (minimal records-read op in both SDKs): Classification: spec_gap. Owner: A7. Recorded-by: orchestrator.

---

## Amendment 8 - 2026-07-10 - manifest intent extension for lossless P6.6 parity (spec_gap)

F4's pilot requires post-normalization deep equality with the legacy P6.6 normalized lane; the verbatim §4.1 schema cannot express the needed author intent. Resolution in three classes; NO generic legacy blob is permitted.

**(i) Author intent — `bb.e4.lane_manifest.v1` is EXTENDED (same schema ID; campaign-internal iteration). The amended schema JSON committed by F4 is authoritative over §4.1's verbatim block:**
- `normalize.record_roles` object<string,string> — semantic record→artifact-role assignment (legacy /normalize/config/packet_constants/record_roles).
- `normalize.record_envelopes` object<string,object> — authored grouping/envelope recipe.
- `normalize.role_aliases` object<string,string> — authored semantic aliasing.
- `normalize.auto_bind_role_refs` boolean — authored auto role-ref binding choice.
- `normalize.scope_observation_labels` array<string> — authored observation labels projected into claim scope.
- `ct.test_id` string|null — human-assigned CT identity.
- `acceptance` REPLACED with the legacy v2 authored contract shape: `{behavior_family: string, semantic_key: string, target: string|null, assertions: [{id, description}]}` (§4.1's behavior_families/notes cannot express it and is not mechanically recoverable).

**(ii) Deterministically derivable — stays OUT of the manifest; the compiler/normalizer documents each mapping and a pilot test covers it:** target field renames; status mapping (`accepted`↔`accepted` exact; `candidate`/`draft`→`captured`/`planned` one-way — parity is asserted ONLY for `accepted`); normalize.mode/translator assembly; required_records/required_roles passthrough; compare-assertion field rename/unwrap (`{assertion_id,kind,description,record_selector.path,expect}`→`{id,path,op,value,description}`); replay `mode=stored`→`session=null` + comparator_class copy; claim exclusion `{id,reason}`→string; metadata synthesized (non-normative); run derived from declared support-claim input scope; provenance derived from target freeze row. **`provenance.source_paths` ordering/filtering MUST be explicitly specified in the compiler (documented, deterministic, pilot-exact) and covered by a test — no ad hoc logic.**

**(iii) Machine-owned — never in the manifest:** `normalize.config.roles`→lock `artifact_roles`; `payload_templates`+`substitutions`→generated packet_constants sidecar (F3's sidecar stays exactly two keys); all digests/pins/hashes→lock `resolved_inputs`/`registry_pins`/`target_freeze` or generated sidecar.

**Classification:** spec_gap. **Owner:** F4 (amended schema + compiler mappings + pilot parity test; F1 schema file and its tests updated in the F4 commit citing AM8). **Recorded-by:** orchestrator.

---

## Amendment 9 - 2026-07-10 - canonical directory-tree digest semantics (spec_gap; resolves AM5 gap 3)

Directory inputs are legitimate adapter inputs (e.g. P6.6 `raw/`, `joined_sessions/`, `detached_sessions/`, source-freeze dir); replacing them with expanded file lists would hide compaction logic. Campaign-wide definition, used by every consumer (F4 lock `resolved_inputs`, H3 stored-replay reuse provenance):

- **Tree digest**: `sha256:` over the canonical-JSON (sorted keys, no whitespace, UTF-8) preimage `{"files": [{"path": <repo-relative posix path>, "sha256": <file digest>, "bytes": <file size>}...]}` with entries sorted by `path` bytewise ascending. Only regular files participate; symlinks/dirs-as-entries are rejected fail-closed; empty directories digest the empty list.
- **`bytes` for a directory input** = sum of member file bytes.
- **Single implementation**: one shared helper module owned by **F4** (committed in the wsF3 packet; exact path declared in F4 evidence). **H3 MUST consume that helper, never reimplement** — if H3 needs it before WS-F3 merges, H3 cherry-picks the helper commit as a declared dependency (dep_commits in evidence), same pattern as G4/F3.

**Classification:** spec_gap (completes AM5 gap 3). **Owner:** F4 (definition+helper); H3 (consumer). **Recorded-by:** orchestrator.

---

## Amendment 9a - 2026-07-10 - tree-digest domain completion (addendum to AM9; recorded before any consumer implementation)

AM9's definition is completed as follows; the F4 helper implements EXACTLY this:

- **Root & paths**: entries are all regular files recursively under the digested directory root. `path` = path RELATIVE TO THAT ROOT (content-addressed; the input's own repo location is recorded separately by the consumer, e.g. lock `resolved_inputs[].path`). POSIX `/` separators; path components are the raw on-disk bytes decoded as strict UTF-8 — undecodable names fail closed. No unicode renormalization (NFC/NFD as stored).
- **Ordering**: entries sorted bytewise ascending over the UTF-8 encoding of `path`.
- **Canonical JSON preimage**: UTF-8; object keys sorted; separators `,` and `:` (no whitespace); `bytes` as JSON integer; `sha256` as bare lowercase 64-hex; no trailing newline. Digest output format: `sha256:<lowercase hex>` over that preimage.
- **Membership**: every regular file participates — including dotfiles; NO exclusion policy (determinism over convenience; frozen artifact dirs). Hard links hash as regular files. Empty directories contribute nothing; an empty root digests `{"files":[]}`.
- **Fail-closed set**: symlinks (any, file or dir — never followed), FIFOs/sockets/devices, unreadable files, undecodable names. Additionally each entry's realpath MUST remain under the root's realpath (no escape), else fail closed.

**Classification:** spec_gap (completes AM9). **Owner:** F4 (helper); H3 (consumer). **Recorded-by:** orchestrator.

---

## Amendment 10 - 2026-07-10 - I2 acceptance gate + freeze tightening governance (gate_wrong)

1. **I2 gate.** I2's literal ACCEPT `pytest -q tests/compilation` is red at base 27deb570 for pre-existing product defects unrelated to consolidation (v2_loader `_config_metadata` injection, dossier hash drift, kernel emitter drift; 175 failures — the same set exec-verified during WS-D's pre-existence audit). Replaced by: (a) the four named guarding suites, UNMODIFIED by the packet, green; (b) the new schema-first suite green; (c) **no-regression clause**: the `tests/compilation` failure set (by test ID) at the packet head must be a subset-or-equal of the base failure set — verifiers rerun both trees and diff the sets. I2 is NOT required to fix pre-existing failures.
2. **Freeze tightening governance.** FREEZE_POLICY.md promised per-occurrence allowlisting of existing-schema tightenings inside check_phase20_freeze.py; no such mechanism exists (the script inventories semantic-ID additions only — B2's verified scope). Corrected governance: constraint tightening of an existing schema is permitted when a plan packet requires it, and each occurrence MUST be (a) named in that packet's ledger-item evidence with the packet id, (b) covered by a red-gate test proving the tightened constraint rejects what it should, (c) reviewed by the packet's verifier. The central freeze script continues to enforce ID-level additions only. FREEZE_POLICY.md is corrected in the commit recording this amendment.

**Classification:** gate_wrong (both parts). **Owner:** I2 (gate); freeze policy text: orchestrator bookkeeping. **Recorded-by:** orchestrator.

**AM10 clarification (same date):** the no-regression comparison is over normalized failing-test IDENTITIES AND OUTCOMES, never counts: nodeid (absolute paths stripped) + outcome class (failed vs error/collection-error) + normalized exception type/message-head. The base tree is PINNED by SHA = the packet branch's merge-base with integration at verification time, recorded in the verifier report alongside both full failure sets. HEAD's set must equal or be a strict subset of base's; any new identity, changed outcome class, or changed exception identity = fail. Packet-specific and new suites remain fully green — the subset rule applies ONLY to the pre-existing `tests/compilation` baseline.

**AM10 part 2 REVISED (same date; supersedes the governance paragraph above):** normalizing the policy prose down to current script behavior was the wrong fix — it silently weakened a written enforcement contract (exactly the words!=behavior failure mode this campaign targets). FREEZE_POLICY.md's original promise is RESTORED verbatim. The missing mechanism is reclassified **product_defect against the B-packet policy contract** and the promised mechanism WILL be implemented in the freeze gate: the baseline records per-schema content hashes; any content drift of an existing schema is red UNLESS `TIGHTENING_ALLOWLIST` in scripts/check_phase20_freeze.py carries `{schema_id: packet_id}` for it; negative tests cover unallowlisted drift (red), allowlisted drift (green), and addition detection unchanged. Owner: WsBFreeze (B2 follow-up commit), verified before merge; packet-evidence naming + red-gate tests from the superseded paragraph REMAIN as additional obligations on tightening packets. AM10 part 1 (I2 gate) is unchanged.

---

## Amendment 11 - 2026-07-10 - P6.6 source-freeze input pinned as tracked archive (spec_gap)

The pilot's declared source-freeze directory `docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest` contains symlinks (`node_modules/.bin/*`), so the AM9/AM9a tree digest fails closed — correctly. The frozen directory is a historical artifact and MUST NOT be mutated. Resolution for the pilot manifest:

- The manifest declares the existing tracked regular file `docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_git_tracked.zip` as the source-freeze input; the lock pins its ordinary file sha256/bytes.
- The normalizer maps THAT EXACT archive ref to the legacy directory path string in the runtime lane dict only (documented, deterministic, pilot-exact, covered by a test) — the runtime adapter behavior is unchanged.
- **Recorded limitation:** equivalence between the pinned zip (git-tracked subset of the freeze) and the legacy directory (which additionally contains untracked node_modules content) is NOT machine-verified; the lock's provenance claim covers the archive only. Post-normalization deep-equality parity still holds because the legacy dict receives the mapped directory string.
- All other directory inputs (e.g. raw/, joined_sessions/, detached_sessions/) remain under AM9/AM9a tree digests; if any of them also fails closed, that is a NEW stop-and-report, not a silent extension of this exception.

**Classification:** spec_gap. **Owner:** F4. **Recorded-by:** orchestrator.

---

## Amendment 11a - 2026-07-10 - AM11 revision: extraction equivalence MUST be machine-verified (supersedes AM11's "recorded limitation")

AM11's "equivalence not machine-verified" clause is WITHDRAWN: the lock must never attest to bytes the runtime does not consume. Revised contract for the pilot source-freeze input:

- **Source of truth:** the tracked archive `docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_git_tracked.zip`; the lock pins its file sha256.
- **Deterministic extraction:** the compiler extracts the pinned archive to a derived, plan-named path (`docs_tmp/phase_20/derived/oh_my_pi_main_5356713e_extracted/`), fails closed if the extraction contains any symlink/special file or undecodable name, computes the AM9/AM9a tree digest of the extraction, and records BOTH digests (archive sha256 + extraction tree digest) in the lock's resolved input entry.
- **Runtime consumes the extraction:** the normalized runtime lane dict references the extraction path — the runtime input IS the content the lock attests, via the recorded digest chain (zip sha256 -> deterministic extraction -> tree digest).
- **Parity mapping:** for post-normalization deep-equality against the legacy lane dict ONLY, the declared mapping substitutes the legacy directory string for this one input (documented, pilot-exact, tested). The legacy frozen directory itself stays untouched and is consumed by nothing in the new flow.
- **Re-verification:** `--check` mode re-extracts (or re-digests the existing extraction) and fails on any digest mismatch; the derived extraction is disposable/reproducible, never hand-edited.
- Other directory inputs remain under AM9/AM9a tree digests; a new fail-closed occurrence is a stop-and-report.

**Classification:** spec_gap (revision). **Owner:** F4. **Recorded-by:** orchestrator.

**AM11a clarification (same date):** the tracked zip is the SOLE prerequisite — a clean checkout with a valid lock must work: both compile and `--check` MUST materialize the extraction themselves when absent (and re-digest when present), never assume it exists. Canonical safe extraction is normative: reject archive entries with absolute paths, `..` components, symlink/special-file entries, or undecodable names (fail closed); extraction writes regular files only, permissions normalized. Tests MUST cover extraction-from-clean-state (derived dir deleted, then compile/--check green) in addition to the fail-closed set.

---

## Amendment 12 - 2026-07-10 - shared dependency commits (spec_gap; generalizes the F3->G4 pick)

The one-commit-per-item rule governs ITEM commits. A **shared dependency node** is a distinct commit class, permitted when two packets need the same artifact (AM9 tree_digest helper: F4 authors, H3 consumes):

- The owning workstream lands the artifact + its focused tests as ONE standalone commit touching ONLY the shared artifact paths; it is not the owner's item commit.
- The owner's later item commit MUST NOT re-modify the shared artifact files (they are excluded from its diff).
- Consumers cherry-pick the EXACT dependency commit (never reimplement, never re-derive); evidence records it as `declared_dependency: <sha>`.
- At integration, the merger verifies patch-id equivalence and tree identity on the shared paths across every branch that carries the commit; divergence is a red gate.
- Applies retroactively to F3 54147aaa -> G4 pick 04b11cb5 (same shape, already patch-id-bound).

**Classification:** spec_gap. **Owner:** orchestrator (rule), WsF3Compiler (tree_digest node). **Recorded-by:** orchestrator.

---

## Amendment 13 - 2026-07-10 - F5 full-flow gate depends on WS-H stage semantics (spec_gap: plan dependency table lists only F4)

F5(a)'s end-to-end run reaches capture executed_pass but normalize fails at base head (`did not execute and has no author declaration`) because stage semantics land in H2/H3/H4. The plan's F5 dependency column is corrected: F5(a) additionally depends on H2-H4.

**Resolution is SEQUENCING, not dependency-picks:** whole H packets must not be cherry-picked into wsF3 (packet verification boundaries stay intact; AM12 covers only single shared-artifact commits). Order: WS-H verifies + merges into integration first; wsF3 then rebases onto that head and completes F5(a)'s full-flow gate. F5's other sub-gates (regen/churn, lock_sha256 propagation in run reports — authorized, in-scope) proceed now. F5's ACCEPT is unchanged; only its prerequisite set is corrected.

**Classification:** spec_gap. **Owner:** F5 (WsF3Compiler), sequencing by orchestrator. **Recorded-by:** orchestrator.

---

## Amendment 14 - 2026-07-10 - P6.6 sidecar source promotion (spec_gap; resolves F6 regeneration cycle)

The pilot sidecar's `payload_templates`/`substitutions` blocks were migration-extracted from legacy YAML and are derivable from NO manifest-referenced input; once legacy retires (F5) the sidecar would be unregenerable, and reusing the sidecar as its own source is cyclic. Content underivable from declared inputs is AUTHOR INTENT by definition, so it moves to the intent layer — we do not invent a reverse-derivation generator:

- **One-time promotion (F6 item commit):** extract the two blocks into a repo-tracked, author-owned source file at a plan-named path next to the pilot manifest (e.g. `config/e4_lanes/oh_my_pi_p6_0_l5_memory_compaction.payloads.yaml`); the manifest references it as an input; the lock pins its digest like any other resolved input.
- **Compile derives the sidecar deterministically from that source** (normalization allowed; the machine never writes the source). `--check` verifies sidecar == f(source, manifest).
- **Migration equivalence gate:** the sidecar compiled from the promoted source must be byte-identical to the currently accepted sidecar ONCE at migration time; that proof recorded in F6 evidence, then legacy retirement (F5) may proceed.
- Compile mode purity is unchanged: no legacy reads in steady state; no machine-owned shadow sources; the promoted file is ordinary authored input from then on.

**Classification:** spec_gap. **Owner:** F6 (WsF3Compiler). **Recorded-by:** orchestrator.

---

## Amendment 15 - 2026-07-10 - universal clean-checkout input rule (generalizes AM11a; G6 finding classified product_defect on F4)

G6's clean-state gate exposed the pilot manifest declaring capture input `artifacts/conformance/node_gate/ct_p6_oh_my_pi_p66_task_job_subagent_c4_chain.json` — untracked (`git ls-files` empty), absent on clean checkout; `lane lock --check` exits 3.

**Rule (normative for every manifest, not just the source-freeze input):** every declared input MUST resolve on a clean checkout as exactly one of:
1. a repo-TRACKED file/directory (digest-pinned in the lock), or
2. a DERIVED input materialized deterministically by the compiler from tracked sources under a declared derivation rule (AM11a pattern), digest-chain recorded in the lock.
Anything else is a red gate at lock/--check time (exit 3 behavior confirmed correct).

**F4 disposition (owner WsF3Compiler):** classify that path — if the lane CONSUMES it, replace with a tracked canonical input (or a declared derivation from tracked sources); if it is actually an OUTPUT of the capture chain, remove it from inputs and place it in the artifact/output roles. Then regenerate the lock and rerun `migrate --check`. G6 stays blocked on this fix; its refusal to weaken the gate is correct.

**Classification:** product_defect (F4 manifest content) + spec_gap (rule not yet written). **Recorded-by:** orchestrator.

**AM14 addendum (AM14a, same date):** legacy payload-block retirement is gated on MORE than sidecar byte-equality: (1) consumer inventory proving compiler/runtime read ONLY the promoted manifest-referenced source; (2) schema/path validation of the promoted file; (3) its digest pinned in the lock; (4) legacy-vs-new SEMANTIC output parity (normalized lane dict), not just sidecar bytes; (5) the one-time extractor is a committed, reproducible script — the authored file must not be an unverifiable manual transcription.

---

## Amendment 16 - 2026-07-10 - pilot manifest input surface: clean-checkout sources only; sidecar derives runtime payloads; parity-only legacy mapping (spec_gap; subsumes the AM15 F4 disposition, resolves F6 jointly with AM14)

F4's deeper defect: the pilot manifest references downstream run/evidence artifacts (node_gate chains, target captures, session dirs — untracked/ignored), violating the manifest<-lock<-evidence direction and the AM15 rule. `--check` recompiles every input digest per F3's contract and MUST NOT trust recorded pins for missing inputs (WsF3Compiler's refusal to weaken is correct). Authorized redesign:

- **Manifest inputs = clean-checkout sources ONLY** (tracked zip per AM11/AM11a, scripts, schemas, config, the AM14 promoted payloads source). No run/evidence artifact may appear as a manifest input.
- **Machine-owned sidecar derives runtime payload inputs** deterministically from the declared sources (+ lock artifact roles); the adapter consumes sidecar-derived payloads directly — no physical untracked/ignored artifact required at runtime. Digest chain recorded in the lock.
- **Parity-only legacy mapping:** post-normalization deep-equality against the legacy lane dict uses a declared expansion of legacy capture.inputs (comparison layer exclusively; never consulted by compile/run/--check).
- `--check` semantics unchanged: recompute EVERY digest from clean-checkout state; missing input = red exit 3.

**Classification:** spec_gap. **Owner:** F4/F6 (WsF3Compiler). **Recorded-by:** orchestrator.

---

## Amendment 17 - 2026-07-10 - lane_def v1/v2 stage-honesty schema evolution (WS-H; classification + freeze authorization)

WS-H's stage-vocabulary work adds to `bb.e4.lane_def.v1` + `.v2`: required `mode` on normalize (`identity|translate`) and replay (`const "stored"`), plus required declared `artifacts` (non-empty, unique, relative-path pattern) on replay. Precise classification: **tightening-with-extension** — a new constrained property is admitted AND made required, so pre-existing documents without the declarations are rejected. This is the plan's stage-honesty mandate (stages either execute or are explicitly declared stored/data-defined; zero silent skips) expressed at the schema layer, and is AUTHORIZED, subject to:

1. **Freeze mechanism compliance (B2b, same-commit rule):** every schema-touching commit carries/updates the hash-pinned `TIGHTENING_ALLOWLIST` entries for both files; the freeze gate is green at each such commit and at the packet head.
2. **Consumer proof:** every lane definition under config/e4_lanes/ validates at the packet head (full lane census, not a filtered subset), or the packet is red.
3. The freeze-policy term "tightening" is READ to include this tightening-with-extension shape only when the added property is required+constrained (pure widenings — optional new fields, enum growth — remain UNAUTHORIZED without a dedicated amendment).

**Classification:** spec_gap (schema-evolution class undefined) + process finding (H2/H3 landed drift without allowlist entries; caught by static verifier + freeze gate red at verify head — the dual-lens design worked). **Owner:** WS-H (WsH2Stages2). **Recorded-by:** orchestrator.

**AM17 revision (AM17a, same date; supersedes AM17's mechanism choice):** the lane_def evolution is NOT routed through the tightening exception — mixed tightening+extension must not ride TIGHTENING_ALLOWLIST semantics. Grounding and mechanism:

- **Necessity (verbatim plan text, no inference):** H2 ACCEPT — "Explicit `normalize: {mode: ...}` required in source"; H3 ACCEPT — "`replay.mode: stored` REQUIRED explicitly in every lane source (loader default REMOVED...) ACCEPT: schema+loader change"; plan §4.1 replay block — `"required": ["mode", "comparator_class"], "mode": {"enum": ["stored"]}`. The schema change is plan-mandated, predating the freeze baseline.
- **Mechanism (B2c, owner WsBFreeze):** allowlist entries gain a `class` field: `{packet, sha256, class: "tightening"|"plan_mandated_evolution", ref}` — `ref` REQUIRED for evolution class, naming the mandating plan item(s)/amendment. Gate behavior unchanged (hash pin authorizes exact content); the record is honest about WHAT was authorized. Bare 2-field entries remain valid as class=tightening for backward compat of already-merged pins; malformed still exit 2.
- **Migration duty (H packet):** consumer/call-site migration matrix in H evidence (lane sources, validate_lane, run_lane/loaders, any generated types reading lane_def), plus FULL unfiltered lane census validating at head. Pure widenings (optional fields, enum growth) remain UNAUTHORIZED without dedicated amendment.

**AM17 second addendum (AM17b, same date):** per-pointer disposition for the replay `artifacts` delta in lane_def.{v1,v2}: `required` + `minItems: 1` + `items: {type: string, minLength: 1}` are AUTHORIZED by derivation necessity (H3 mandates non-empty `reused_inputs` digests sourced from author-declared stored artifacts; absent a required non-empty declaration the runtime must infer paths — a silent-inference defect). `uniqueItems: true` and the relative-path pattern `^(?!/).+` are AUTHORIZED as convention-entailed, not verbatim-mandated: §4.4 fixes stage artifact refs as repo-relative, and duplicate declared artifacts are always an authoring error producing duplicate provenance digests. Recorded explicitly so no constraint rides in silently.
