# BB_RS_MASTER_PLAN — Phase 20 "Right Shape" Campaign
Version: 1.0.0 (2026-07-10) · Authors: Claude (orchestrator) + Kyle McCleary · Executor: GPT 5.6 under the Oh-My-Pi harness with eval-workflow subagents
Reference assessment: `docs/plans/phase_20_right_shape/BB_DIRECTION_ASSESSMENT_20260710.md` (verbatim; Appendix A = adversarially verified fact base)
Progress ledger: `docs/plans/phase_20_right_shape/BB_RS_PROGRESS.json` · Goal prompt: `docs/plans/phase_20_right_shape/phase20_goal_prompt.txt`
Tracking: bd epic to be created as first act of execution (see §1.6). Planning bead: `bb-s5t`.

---

## 0. North star and binding constraints

### 0.1 North star (the thesis this campaign serves)
BreadBoard is a **maximalist engine for agent-harness design**: expressive enough to build any modern harness (research, engineering, or other) and to **clone existing harnesses as config** (E4), on the **cleanest minimal set of expressive primitives**, maximizing — in stated priority order:
1. maintainability, 2. atomicity/composability, 3. generalizability, 4. development velocity, 5. versatility, 6. dev-x, 7. interpretability, 8. intuitive configuration, 9. forward compatibility, 10. minimal blast radius against existing BreadBoard core behavior and existing E4 gains.

### 0.2 The verdict this campaign answers
`right_direction_wrong_shape` (3/3 briefed adversarial judges). The campaign converts the four shape defects into fixed product structure:
(1) "minimal" vocabulary inflation → tiered contract estate; (2) evidence apparatus metastasis (cyclic hash-pin graph, 7,292-line lane descriptors, skipped pseudo-stages) → manifest/lock split + honest stages; (3) starved/broken product seam (SDKs broken vs default server, CI gap, no front door) → repaired vertical slice + product CI + CLI; (4) campaign operating system consuming the project → freeze + external falsifier (flagship demo).

### 0.3 Anti-goals (violations are campaign failures, not judgment calls)
- **NO** lane CRUD API; **NO** E4 exposure in the public SDK surface.
- **NO** TypeScript package reorganization/repackaging campaign.
- **NO** new schema families beyond the THREE specified in §4 (`bb.e4.lane_manifest.v1`, `bb.e4.lane_lock.v1`, `bb.contract_tiers.v1`).
- **NO** new campaign scorecards, ledgers, or evidence snapshot forms beyond `BB_RS_PROGRESS.json`.
- **NO** parallel implementations of existing capabilities: the CLI wraps existing scripts; the SDK repair edits existing clients.
- **NO** silent scope reduction: if an item cannot be completed, it is marked `blocked` in the ledger with a failure classification (§1.5) — never quietly dropped or relabeled.

### 0.4 Prime derailment guard
This is the THIRD direction assessment with the same conclusion (2026-07-04, 2026-07-06, 2026-07-10). The constraint is not knowledge; it is enforcement. Therefore: **every work packet, before merge, is checked against §0.3 by an independent verifier subagent** (§1.4), and the executor re-reads §0 at every phase boundary. If any instruction elsewhere in this plan appears to conflict with §0, §0 wins and the conflict is logged in the ledger `notes`.

### 0.5 What we actually want — the shortcomings in plain language, and the intent behind every prescription (read first; re-read whenever a packet feels ambiguous)

This section is the orchestrator's own thinking, written for the executor to adopt as its own. The checklists below are projections of THIS; when they underdetermine a decision, decide in the direction of this section. When they contradict it, stop and file a spec_gap (§1.5) — do not silently comply with either side.

**What went wrong, honestly.** We built a magnificent verification machine and then mistook operating the machine for building the product. Five campaigns in a row optimized "provable completion of this campaign" — ledgers converging, scorecards at 1000, byte-identical fixed points — instead of "a stranger can pick this up and succeed." Along the way our words drifted from our behavior: "minimal" came to describe a 61-schema catalog of which 12 are load-bearing at runtime; "stages" came to mean metadata rows that mark themselves successfully skipped; "capture as config" came to mean config plus whatever Python the campaign needed that week; a "lane definition" came to mean 300 lines of intent buried in 7,000 lines of machine-written pins. None of this was dishonest — every piece was locally justified — but the cumulative effect is a system that defends itself against change (including good change) and whose published vocabulary cannot be trusted by a newcomer. That last part is the real defect: **a project whose words don't match its behavior cannot be maintained by anyone who wasn't present when the words were written.**

**What we actually want:**
1. **Authoring a harness should feel like the 66-line template.** Small, legible, commented, bootable offline. That feeling — open a file, read it top to bottom, understand your harness — is the product. Every structure we add must preserve it; anything that makes the first hour worse is wrong even if it makes the thousandth hour better.
2. **Cloning a real harness = a small manifest + a bounded adapter + an honest claim.** We accept code at the target boundary — arbitrary external harnesses have arbitrary protocols and pretending otherwise produced the current mess. What we refuse is UNBOUNDED code and UNBOUNDED descriptors: intent must fit in one readable file (≤150 lines), integration code in one reviewable module (≤300 lines), and everything machine-generated must live behind a hash-referenced lock where no human ever reads or edits it.
3. **Words must equal behavior, everywhere, permanently.** A stage either executes or is explicitly declared `stored`/`data_defined` by the author — never silently skipped. "Minimal" counts only what is load-bearing, and the tier registry makes the counting mechanical. A claim names exactly what it proves and excludes. This is not cosmetic: vocabulary honesty is what lets GPT-5.6-class executors (and humans) operate the system without archaeology.
4. **Evidence serves the product; the product does not serve evidence.** The verification machinery exists to make our claims trustworthy — it is the moat, keep it sharp. But its cost must be paid at boundaries (promotion of accepted evidence, release gates), not on every touch of every file. The cyclic pin graph inverted this: it taxed every edit to keep the machine satisfied. The manifest/lock split plus content-addressed pins restores the correct direction of service.
5. **An outsider succeeding is the only completion signal we cannot game.** Internal scores (including this plan's 1000 points) are navigation aids. The flagship demo (WS-J) is the actual test of the thesis, which is why it is the falsifier in the charter and why nothing large (WS-K/L) unlocks before it teaches us what the structures must survive.
6. **Intent must live in the repo, not in chat.** The reason this is the third identical assessment is that the previous two lived in transcripts. This plan, the charter, and the freeze are intent-in-repo. Guard them: the derailment audit (§1.1 step 6) exists to catch the exact failure mode that consumed phases 15–19 — quiet redefinition of "done" by whoever is currently working.

**How to use this section while executing:** before starting any packet, ask "which of the six wants does this serve?" — the answer is in the packet's Intent line. If you cannot connect a proposed action to one of the six, you are probably decorating the machine; stop and re-read §0.3.

---

## 1. Execution contract (normative, for GPT 5.6 under Oh-My-Pi)

### 1.1 Operating loop (the campaign state machine)
```
LOOP until §6 completion criteria all pass:
  1. Read BB_RS_PROGRESS.json → eligible items = status todo|in_progress whose deps (§3 tables) are done.
  2. Select next work packet: highest-weight eligible item; break ties by workstream order A→L.
  3. Execute the packet per its strategy block (§3), using workflow fan-outs (§1.3) where the packet says so.
  4. Produce the packet's evidence artifacts (§1.4). Update the ledger. Commit repo artifacts.
  5. Run the packet's gate command(s). Green → mark done (with verifier verdict). Red → classify failure (§1.5), route per the packet's failure table, log in ledger.
  6. Every phase boundary (a workstream fully done) → derailment audit: one reviewer subagent checks the diff-since-last-audit against §0.3/§0.4; one completeness critic checks ledger vs §6.
```
No stopping between packets. A finished fan-out is a step, not a stopping point. If truly blocked (external credential, human decision), mark `blocked` with classification and MOVE ON to the next eligible packet.

**Interpreter rule (binding for every local gate in this plan):** bare `python` is NOT on PATH in this environment (verified this session). At the start of every packet, resolve the repo-local interpreter once: `PYTHON="$REPO_ROOT/.venv/bin/python"`. If that file does not exist (fresh checkout, e.g. G6's temp env), bootstrap it — `python3 -m venv "$REPO_ROOT/.venv" && "$PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt"` (the repo root has `requirements.txt` but NO `pyproject.toml` until G1 lands; if a gate needs extra test deps, install them explicitly and record it) — and record the bootstrap as environment evidence. `"$PYTHON" -m pip install -e "$REPO_ROOT"` is valid ONLY for G1-and-later packets (G1 creates the root `pyproject.toml`; no `dev` extra exists unless G1 explicitly defines one). Every local command written as `python …`, `pytest …`, or `pip …` in §3/§4/§6 MEANS `"$PYTHON" …`, `"$PYTHON" -m pytest …`, `"$PYTHON" -m pip …`. Only CI workflow YAML snippets (which provision their own interpreter via actions/setup-python) keep bare `python`. Never “fix” a red gate by swapping interpreters silently.

### 1.2 Work Packet Protocol (no self-approval)
Every ledger item is executed as a packet with **two distinct roles, ALWAYS two separate subagents** (an implementer may never verify its own packet, and the orchestrator may never verify inline):
- **Implementer**: makes the change on a branch (or worktree), runs the packet's gates locally, assembles the evidence bundle.
- **Verifier**: a DIFFERENT subagent (reviewer/explore agent) that (a) reproduces the gate commands from the branch head SHA, (b) for bug fixes, first reproduces the failure on the base SHA, (c) checks the diff against the packet's acceptance text AND §0.3, (d) returns a structured verdict `{verdict: pass|fail, head_sha, gates_rerun: [...], anti_scope_violations: [...], notes}`.
An item may be marked `done` ONLY with a verifier verdict of `pass` bound to the exact head SHA recorded in the ledger evidence. The orchestrating agent NEVER verifies its own implementation inline; it spawns the verifier.

### 1.3 Workflow patterns (delegation roles; realize them with whatever subagent/delegation tools your harness provides — e.g. Oh-My-Pi eval-cell helpers or task tools; the ROLES are normative, the tool names are not)
- **Understand**: parallel explore readers with JSON schemas → structured facts. Use before any packet touching >2 files you have not read this session.
- **Implement**: single implementer subagent per packet (isolated worktree when packets overlap files), OR inline edits by the orchestrator for small diffs — but verification is always a separate subagent.
- **Review/Verify**: per §1.2. For high-weight packets (≥30 points) use 2 verifiers with different lenses (correctness; anti-scope) — both must pass.
- **Adversarial**: before marking a workstream done, one skeptic subagent is prompted to REFUTE the workstream's acceptance evidence ("find the gap"); unresolved refutations reopen items.
- Subagents are GPT 5.6: be literal and exhaustive in prompts — exact paths, exact commands, exact output schema, what NOT to do. Pass shared context via files, not prose repetition.

### 1.4 Evidence rules (inherited from house norms; they bind this campaign)
- Exact-scope claims only: name the file, SHA, command, and output. No "should work".
- Every gate run recorded as: command string + exit code + head SHA + (for CI) run URL or local log path under `../docs_tmp/phase_20/evidence/` (workspace-level, sibling of the checkout; scratch) with final refs copied into the ledger.
- Exploratory/scratch work goes under `../docs_tmp/phase_20/` (workspace-level, a SIBLING of the repo checkout — NEVER inside it; untracked). Durable artifacts: repo-tracked paths named in §3/§4 ONLY.
- Never fabricate. A red gate is recorded red. `bd dolt push` at session end.

### 1.5 Failure classification (mandatory on every red gate / blocked item)
`product_defect` (code wrong → fix in-packet) · `spec_gap` (plan/spec ambiguous or wrong → log ledger note, propose spec amendment in `SPEC_AMENDMENTS.md` beside the plan, apply only after amendment is recorded) · `flake` (rerun ×2; if persists, reclassify) · `env` (toolchain/credentials → record exact missing prerequisite, mark blocked, continue elsewhere) · `gate_wrong` (the gate command itself is incorrect → amend via spec_gap path, never silently weaken).

### 1.6 bd integration
First act of execution: `bd create "Phase 20 Right Shape execution" --type epic --priority 1`, then one child issue per workstream (A..L). Close child issues only when the workstream's ledger items are all done/na_gated with verifier verdicts. `bd dolt push` at each session end.

### 1.7 Session bootstrap (every session of the executor)
1. `git -C breadboard_repo_integration_main_20260326 status --porcelain` and current branch/SHA → ledger note.
2. Re-read §0 of this plan + the ledger.
3. `bd ready` → sync bd state with ledger.
4. Resume the loop at §1.1 step 1.

---

## 2. Progress accounting (weighted; stable denominator)

- **Core denominator: 1000 points**, fixed. Items and weights are enumerated in §3 and mirrored in `BB_RS_PROGRESS.json`. Completion % = sum(done item weights)/1000.
- **Conditional track: 250 points, reported separately** (never merged into the core score): WS-K implementation (150) and WS-L implementation (100). These become `todo` only when their gates (§5) record a PASS decision; until then they are `na_gated` and excluded from all percentages. Points are NEVER awarded for deferral; WS-K/L core points (20/10) pay only for executing the gate-decision protocol itself (evidence-backed evaluation + decision record).
- Ledger update protocol: after every packet — update item status, attach evidence refs + verifier verdict ref, bump `updated_at_utc`, recompute `points_done`, commit the ledger with the packet's commit(s).
- The ledger is the ONLY campaign scoreboard (§0.3 bans new scorecards). Format: §4.8.

---

## 3. Workstreams — the conditional playbook

Legend per item: `[id | weight | deps]` then WHAT/HOW/ACCEPT (gate) / FAIL (routing). All file paths are repo-relative to `breadboard_repo_integration_main_20260326/`. Path convention: any path introduced with a creation verb (New/Commit/author/add) or absent from the tree at plan time is a NEW output; every other named path is an EXISTING input — if an existing input is missing at execution time that is a spec_gap, never a license to create it. Scout-verified facts cited as (S1)..(S8) live in `docs/plans/phase_20_right_shape/SCOUT_FACTS.json`.

### WS-A — Repair the SDK vertical slice [90 pts]
Intent: both advertised SDK clients work against default `create_app()`; the README flow is CI-proven. Fix surface is exactly (S1); do NOT widen it.
Branch note: `/health` is intentionally unversioned — do NOT prefix it. `readSessionFile`/`read_session_file` move to `/v1/sessions/{id}/files/content` (suffix change, not a prefix).
- [A1 | 15 | –] `sdk/ts/src/client.ts`: prefix all versioned calls with `/v1` (sessions CRUD/input/command/files/skills/ctrees, models). ACCEPT: `cd sdk/ts && npm run typecheck` green; grep shows zero unversioned `/sessions`|`/models` literals.
- [A2 | 5 | A1] `sdk/ts/src/client.ts` readSessionFile → `/v1/sessions/${sessionId}/files/content`. ACCEPT: focused unit test asserts exact URL.
- [A3 | 10 | A1] `sdk/ts/src/stream.ts` SSE URL → `/v1/sessions/${sessionId}/events`; keep envelope parsing per (S1) sse_notes. ACCEPT: stream unit test green.
- [A4 | 15 | –] `breadboard_sdk/client.py`: prefix legacy session/model calls incl. multipart attachment + streaming URLs; keep `/health` and already-correct `/v1/provider-auth/*`. ACCEPT: `python -m pytest -q` on the SDK's focused tests green.
- [A5 | 5 | A4] `breadboard_sdk/client.py` read_session_file → `/v1/sessions/{session_id}/files/content`. ACCEPT: unit test asserts URL.
- [A6 | 15 | A1..A5] Update SDK tests/fixtures to assert exact `/v1` URLs (files vs files/content, multipart, SSE, unversioned /health, provider-auth). ACCEPT: full `sdk/ts` test suite + Python SDK tests green.
- [A7 | 15 | A6] New default-server integration smoke: boot `create_app()` (no BREADBOARD_LEGACY_ROUTES), run the README create-session→send-input→read-records→stream-events flow through BOTH SDKs (TS via node script, Python via pytest). Place at `tests/test_sdk_v1_default_server_smoke.py` + `sdk/ts/test/defaultServerSmoke.test.ts`. ACCEPT: both green locally.
- [A8 | 10 | A7] Verifier packet (§1.2): independent verifier reruns A1–A7 gates from branch head; base-SHA reproduction of the original break (both SDKs fail against default server pre-patch). ACCEPT: verdict pass.
FAIL routing: DTO mismatch discovered → classify spec_gap, amend §4.6 table, fix; server-side route absent → STOP (do not add server routes; anti-goal) and re-check (S1) mapping — the OpenAPI is the truth.

### WS-B — Freeze + governance cap [45 pts]
Intent: stop surface growth while the campaign runs; make the freeze machine-checkable.
- [B1 | 15 | –] Commit `docs/plans/phase_20_right_shape/FREEZE_POLICY.md` with §4.10 text VERBATIM. ACCEPT: file present, referenced from plan and charter.
- [B2 | 20 | B1] Freeze gate script `scripts/check_phase20_freeze.py`: compares SEMANTIC inventories against a recorded baseline (committed JSON snapshot), not file globs: (a) the set of schema `$id` values across ALL published schema roots (`contracts/kernel/schemas/**`, `docs/conformance/schemas/**`); (b) SDK package identities (name+path from every `package.json`/`pyproject.toml` under `sdk/` and `breadboard_sdk/`); (c) lane IDs and lane `kind` values across `config/e4_lanes/*`; (d) top-level ledger/scorecard files. Fails on any set ADDITION not in the in-script allowlist (the three §4 schemas, the pilot manifest/lock, M-track files). Wire into product-spine CI job (WS-D). ACCEPT: green on clean tree; red on EACH negative test: (i) new dummy schema file, (ii) `$id` change inside an existing schema file, (iii) new lane `kind` value edited into an existing lane, (iv) new sdk package.json — each via tmp worktree, evidence logged.
- [B3 | 10 | B1] bd epic + child issues per §1.6; announcement note in ledger. ACCEPT: `bd ready` shows the epic; ledger note carries issue ids.
FAIL routing: legitimate need for a new schema → spec_gap amendment with explicit user sign-off recorded in SPEC_AMENDMENTS.md; otherwise the freeze holds.

### WS-C — Direction charter [45 pts]
Intent: one durable page that defines the product boundary and the falsifiable thesis; ends "done" redefinition.
- [C1 | 15 | –] Commit `docs/DIRECTION_CHARTER.md` with §4.7 text VERBATIM (fill the two TBD numbers only from WS-J's predeclared budget doc when available; until then keep the defaults given).
- [C2 | 10 | C1,J1] Thesis numbers: manifest ≤150 lines, adapter ≤300 lines, one working day — confirmed or amended ONLY by WS-J J1 evidence; record decision in ledger.
- [C3 | 10 | C1] Link charter from `docs/INDEX.md` (authoring section) and `README.md` (one line under the tagline). ACCEPT: both links resolve; `git grep DIRECTION_CHARTER` shows 2 referring files.
- [C4 | 10 | C1..C3,E3] Verifier: independent subagent confirms charter text == §4.7 (modulo C2 numbers), links live, no other doc contradicts the boundary (grep for "minimal expressive harness primitive language" outside charter scope — E3 is a dependency precisely so this holds). ACCEPT: verdict pass.
FAIL routing: J1 evidence missing → C2 blocked (env) until J1 lands, not guessed; charter text drift vs §4.7 → product_defect, restore verbatim; broken links → fix in-packet; contradictory language found elsewhere → small fix packet ≤5 pts charged to WS-C, or freeze-allowlisted historical doc note.

### WS-D — Product CI spine [95 pts]
Intent: merges are gated by the product spine; the heavy battery runs nightly/touched-path. Middle position between judges (assessment §4).
- [D1 | 30 | –] New job `product-spine` in `.github/workflows/ci.yml` running, in order (S4): `pytest -q tests/test_e4_api_surface.py`; `pytest -q tests/compilation`; `pytest -q tests/e4_parity/test_validate_lane.py tests/e4_parity/test_config_explanation.py tests/e4_parity/test_regen_front_door.py`; `python scripts/release/export_cli_bridge_contracts.py && git diff --exit-code docs/contracts/cli_bridge`; `pytest -q tests/test_cli_bridge_contract_exports.py`; `python scripts/check_phase20_freeze.py`. ACCEPT: job green on a no-op PR branch run (evidence: run URL/log).
- [D2 | 20 | D1] New workflow `.github/workflows/e4-battery.yml`: nightly cron + `workflow_dispatch`, runs `pytest -q tests/e4_parity` and `python scripts/e4_parity/regen.py fixed-point --json artifacts/conformance/e4_regen_fixed_point.json` (S4 drift gap). ACCEPT: one green dispatch run recorded.
- [D3 | 15 | D1] Touched-path trigger: `product-spine` always; battery additionally on PR when paths match `scripts/e4_parity/**|tests/e4_parity/**|contracts/kernel/**|config/e4_lanes/**`. ACCEPT: workflow YAML expresses path filters AND one real PR touching a matched path triggers the battery and that run is green (run URL; this is the §6.3 PR-triggered green run).
- [D4 | 15 | D1] Canonical exporter alignment: ci.yml invokes `scripts/release/export_cli_bridge_contracts.py` (not the compatibility wrapper) everywhere (S4). ACCEPT: grep shows no wrapper invocation left in ci.yml.
- [D5 | 15 | A7,D1] Add WS-A default-server SDK smokes to `product-spine`, then ATTEMPT to configure `product-spine` as a required status check on the default branch (`gh api repos/{owner}/{repo}/branches/{branch}/protection` or repo settings). ACCEPT (all in executable scope — D5 is completable without admin rights): job green including smokes; total product-spine runtime ≤ 10 min (record measured duration); AND exactly one of: (i) required-check activation evidenced (gh api output), or (ii) the attempt's failure output captured + a WAIVER recorded in SPEC_AMENDMENTS.md naming the missing credential, the exact command a human must run, and explicit human sign-off — a bare blocked note NEVER satisfies this criterion.
FAIL routing: runtime >10 min → move the slowest non-spine member to battery (record decision); flaky member → classify flake ×2 then quarantine WITH ledger note + reopen item.

### WS-E — Consumer-backed contract inventory + retiering [115 pts]
Intent: §0.5 want #3 — make "minimal" mechanical. Every published schema carries a tier and a named consumer, or it is frozen.
- [E1 | 25 | B1] Commit `contracts/kernel/registries/contract_tiers.v1.json` conforming to §4.3. The census is GENERATED, never hand-counted: enumerate every schema id from `contracts/kernel/packs.v1.json` (verified at plan time: 61 kernel = 49 non-runtime + 12 runtime_protocol; 13 E4 → tier `evidence`; 3 program schemas — `bb.atomic_feature_ledger.v1`, `bb.er.progress.v1`, `bb.unsupported_case.v1` — assign explicit tier+disposition; 14 legacy_frozen) plus the 3 new §4 schemas (tier `config_algebra`) → expected 94 entries at the Phase 20 baseline. If the live enumeration differs from 94, the GENERATED set wins and the actual count is recorded in evidence. Every entry: ≥1 consumer path or `disposition: freeze`. ACCEPT: file validates against §4.3 schema (add the schema file itself at `contracts/kernel/schemas/bb.contract_tiers.v1.schema.json`); registry schema-id set == generated census set (set equality, checked by script, both directions); validation snippet in packet evidence.
- [E2 | 30 | E1] Adversarial consumer audit: for every entry with confidence != high in S5a or `consumers==[]`, an explore subagent verifies/refutes the tier with grep evidence; corrections applied. ACCEPT: zero entries with empty consumers AND disposition==keep; audit transcript in evidence dir.
- [E3 | 15 | E1] `contracts/kernel/packs.v1.json`: replace the "minimal expressive harness primitive language" claim with tier-qualified language pointing at the tier registry; do not change pack membership (blast radius). ACCEPT: `git grep -l "minimal expressive harness primitive language"` → only historical docs/evidence (allowlist in packet), not packs.v1.json/README.
- [E4 | 5 | E3] Regenerate `contracts/kernel/schemas/README.md` sections that restate the claim; add tier column sourced from the registry. ACCEPT: README table row count == registry entry count.
- [E5 | 20 | E1,E2] Consumer evidence links: for runtime_protocol + config_algebra tiers, each entry's consumers[] verified to file:line by a verifier subagent (spot-check 100%; these two tiers are the product spine). ACCEPT: verifier verdict pass.
- [E6 | 20 | E1..E5] Workstream skeptic (§1.3 Adversarial): attempt to refute "every kept schema has a real consumer"; unresolved refutations reopen E2. ACCEPT: skeptic report with zero unresolved refutations.
FAIL routing: schema with no consumer but plausibly product-critical → tier `config_algebra` is NOT a dumping ground; classify honestly as freeze + note; unfreezing later is cheap, unearned "keep" is not.

### WS-F — Manifest/Lock pilot on the P6.6 lane [150 pts]
Intent: §0.5 wants #2/#4 — split intent from machine output on the worst-case lane (7,292 lines: ~300 intent / ~6,992 machine per S2), breaking the pin-churn cycle at its densest node. THREE-LAYER RULE (normative): manifest = intent (author-owned, no hashes); lock = deterministic resolution (machine-owned, canonical bytes, NO timestamps, NO run outputs, NO stage results); run/evidence = existing E4 records (stage reports, claims, evidence manifests) which reference the lock by digest. Chain: manifest ← lock ← run/evidence. A layer NEVER references a downstream layer.
- [F1 | 20 | B1,C1] Commit `contracts/kernel/schemas/bb.e4.lane_manifest.v1.schema.json` with §4.1 EXACTLY (it is authoritative; do not redesign). ACCEPT: jsonschema Draft2020-12 self-check green; AND a `validate_lane.py` loader test proves rejection of `sha256:` appearing ANYWHERE in a manifest (fixtures exercising notes/description/argv/inputs/cwd and one nested location) — the per-field pattern guards in the schema are defense-in-depth; the LOADER is the enforcement layer (§4.1 design rule a).
- [F2 | 20 | F1] Commit `contracts/kernel/schemas/bb.e4.lane_lock.v1.schema.json` with §4.2 EXACTLY. ACCEPT: self-check green; NO volatile fields (no *_at_utc, no durations) — CI grep in freeze script extended to enforce.
- [F3 | 35 | F1,F2] Compiler `scripts/e4_parity/compile_lane_lock.py`: input manifest path → deterministic lock JSON (canonical separators, sorted keys) + generated sidecar `<lane>.packet_constants.v1.json` holding what today lives in `/normalize/config/packet_constants/payload_templates` and `/substitutions` (S2 biggest blocks); lock stores sidecar digest. Two explicit modes: `migrate` (one-time, F4/K2 only: reads the LEGACY YAML descriptor as a declared input — its path+digest recorded in the lock's inputs — and extracts payload_templates/substitutions into the sidecar) and `compile` (steady-state: sidecar content derives ONLY from files/registry entries referenced by the manifest, every input digest recorded in the lock; never reads legacy YAML). `--check` recompiles in the recorded mode and byte-compares lock+sidecar (exit 5 on drift). Determinism: two consecutive runs byte-identical (gate). ACCEPT: unit tests incl. determinism + `--check` drift detection; run on a synthetic mini-manifest fixture.
- [F4 | 35 | F3] Pilot migration: author `config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml` (pilot allowance ≤300 lines per §4.1 budget semantics — the ≤150 thesis number belongs to the flagship demo lane ONLY and is never reset by this pilot; sourced from the lane's author_intent fields per S2 list) + compiled lock + sidecar; teach `lane_definitions.py` loader to accept (manifest+lock) as an alternate source resolving to the SAME normalized lane_def dict the runtime consumes today. Old YAML stays until F5 proves parity, then is replaced by a pointer file or removed (decision recorded). ACCEPT: loader round-trip test — normalized dict from (manifest+lock+sidecar) == normalized dict from legacy YAML (deep equality, allowlisted field order).
- [F5 | 25 | F4] Parity + churn proof: (a) `run_lane.py --lane oh_my_pi_p6_6_task_job_subagent` full stage set + C4 claim reverify green from the new source; (b) `regen.py fixed-point` green; (c) churn probe: touch an unrelated watched file, re-run refresh — lock/sidecar unchanged (byte-identical), demonstrating pin-locality. ACCEPT: all three with logged commands + SHAs.
- [F6 | 15 | F5] `refresh_lane_descriptor_pins.py`: for migrated lanes, rewrite ONLY the lock+sidecar (never the manifest); assert-fails if asked to touch a manifest. ACCEPT: unit test + one refresh run evidence.
FAIL routing: F4 round-trip inequality → classify: missing field in §4.1/4.2 → spec_gap amendment (add field, document); generated payload needed at runtime → it belongs in the sidecar, not the lock or manifest. F5(c) churn probe fails → locate the replicated pin, move it behind the sidecar digest; if the cycle crosses into ledger/catalog, record as WS-K input evidence (do NOT expand pilot scope).

### WS-G — Authoring front door CLI [110 pts]
Intent: §0.5 want #1 — one command path over existing scripts (S6: no root pyproject; `breadboard` bin name is owned by the TUI package). CLI name: `bbh` (BreadBoard Harness) to avoid colliding with the TUI binary; a rename to `breadboard` is a post-campaign decision.
- [G1 | 20 | –] Root `pyproject.toml` — explicit packaging contract: build backend `setuptools`; `[tool.setuptools] packages` enumerated EXPLICITLY (`scripts` + only the top-level packages the CLI actually imports — no auto-discovery across the repo's many top-level trees); NO `dependencies` list (the environment is provisioned via requirements.txt per the §1.1 interpreter rule; adding a dependencies list = packaging campaign = anti-goal); console_scripts `bbh = scripts.breadboard_cli:main`. Plus `scripts/breadboard_cli.py` skeleton with the §4.5 command table + exit codes. ACCEPT: `"$PYTHON" -m pip install -e . && "$REPO_ROOT/.venv/bin/bbh" --help` shows the two namespaces (`harness`, `lane`) and each namespace's help lists its four §4.5 leaves (eight total); AND a fresh-temp-venv editable-install test (venv → install requirements → `pip install -e .` → `bbh --help`) passes.
- [G2 | 30 | G1] `bbh harness init|validate|explain` + `bbh lane init|validate` delegating per §4.5 (harness namespace = product config; lane namespace = E4 manifest — never conflate). ACCEPT: E2E script: harness init → validate → explain AND lane init → validate on a temp dir, exit 0; bad input exits 2 with pointerful message.
- [G3 | 25 | G1,A7] `bbh harness run PATH`: `--server URL` drives /v1 session flow via breadboard_sdk (create→input→records→events); `--local` boots create_app() in-process (uvicorn) then same flow. ACCEPT: `bbh harness run agent_configs/templates/minimal_harness.v2.yaml --local --task "list files"` completes a session offline, exit 0, prints record count.
- [G4 | 15 | G1,F3] `bbh lane lock PATH [--check]` → compile_lane_lock passthrough; `bbh lane capture MANIFEST` → run_lane capture stage for manifest-migrated lanes. ACCEPT: lock --check exit codes 0/5 verified on pilot lane; AND `bbh lane capture` run on the migrated pilot manifest into a scratch out dir produces the expected capture report artifact with exit 0.
- [G5 | 10 | G2..G4] `docs/authoring/AGENT_CONFIG_AUTHORING.md` + `docs/INDEX.md`: replace inline script invocations with bbh equivalents (keep script paths as appendix). ACCEPT: docs updated; every bbh example copy-pasteable (verifier runs each).
- [G6 | 10 | G5] Verifier packet: independent E2E transcript of init→validate→explain→run→lock→capture on a fresh temp checkout env (bootstrap per §1.1 interpreter rule). ACCEPT: verdict pass with full command log.
FAIL routing: `"$PYTHON" -m pip install -e .` conflicts with repo env assumptions → fallback `"$PYTHON" -m scripts.breadboard_cli` documented everywhere and G1 gate amended via spec_gap (name the conflict evidence).

### WS-H — Honest stage vocabulary [120 pts]
Intent: §0.5 want #3. Stages execute or are author-declared stored/data-defined; zero silent skips. Migration-compatible with today's adapter signature (S7): `capture(lane_def, inventory_lane, *, promote_accepted=..., out_dir=...)`.
- [H1 | 30 | –] `run_lane.py`: emit a §4.4 StageReport for every stage; implement `check_stage_report` exactly per its docstring; delete `_metadata_stage_result` success-skip semantics. Non-execution is honest ONLY as `reused_stored_result` (manifest_rule + reused_inputs digests), `disabled_by_manifest`, or `not_applicable` per STAGES_BY_KIND; anything else = `executed_fail`. ACCEPT: unit tests: declared-stored lane passes with `reused_stored_result` + provenance digests; undeclared skip → run fails; existing 21 lanes' declarations updated in the same packet or the battery goes red (that red is the point — fix declarations, not the gate).
- [H2 | 25 | H1] Normalize executes — ALWAYS: run_lane invokes the translator registry entrypoint (S7 identity:translate signature) for every lane, including `normalize: {mode: identity}` (the identity translator RUNS and emits `executed_pass` with a normalize report recording input digests — identity is an executed no-op, NEVER `reused_stored_result`, which is reserved for actual persisted prior results). Explicit `normalize: {mode: ...}` required in source. ACCEPT: one adapter lane (pi_p5_l1) produces an executed normalize report; one identity lane produces an `executed_pass` identity report; battery green.
- [H3 | 20 | H1] Replay honesty — stored replay is the ONLY mode this campaign (no executable replay path exists in the tree; §4.1/§4.4 defer `executed`): `replay.mode: stored` REQUIRED explicitly in every lane source (loader default REMOVED; loader rejects any other value with a pointerful error naming the deferral). Stored → `reused_stored_result` with manifest_rule `/replay/mode` + reused_inputs digests of the stored artifacts. ACCEPT: schema+loader change; all 21 lane sources declare `stored`; loader test proves rejection of `executed` with the deferral message; battery green.
- [H4 | 25 | H1] Compare executes: run_lane loads the decoded capture/replay JSON + claim scope + every declared input_role artifact path, constructs the ComparatorInput mapping EXACTLY per `conformance/comparators/protocol.py`, invokes the registry entrypoint (`conformance.comparators.<module>:compare` from `conformance/comparators/registry.json`), validates and writes the returned comparator report; the C4 claim stage CONSUMES that report rather than re-running comparison. Legacy `compare(*args, **kwargs)` wrappers get the §4.4 keyword-forwarding shim — do NOT bulk-edit adapters. ACCEPT: comparator report artifact exists for an adapter lane run; validate_e4_c4_chain path unchanged and green.
- [H5 | 10 | H1..H4] All 21 lane YAML/manifest sources carry explicit stage declarations; `validate_lane.py` enforces. ACCEPT: `pytest -q tests/e4_parity/test_validate_lane.py` green + full battery green.
- [H6 | 10 | H5] Verifier packet incl. base-SHA reproduction (old silent-skip behavior demonstrated on base). ACCEPT: verdict pass.
FAIL routing: comparator/translator entrypoint signature mismatch → adapters keep legacy signature (S7), wrap via thin shim in run_lane, record in §4.4 notes; NEVER edit all adapters in this workstream (blast radius).

### WS-I — Validation consolidation via gap matrix [70 pts]
Intent: §0.5 want #4; delete duplication only where the schema provably covers it (S3: 25 loader checks: 11 yes / 11 partial / 3 no; 8 helper checks: all no-coverage-because-input-boundary).
- [I1 | 25 | –] Commit `docs/plans/phase_20_right_shape/VALIDATION_GAP_MATRIX.md`: every check from S3 with columns (location, check, schema_covered, cross_field, error-message contract, guarding test, decision keep|delete|schema-first). Helper `_require_*` rows default KEEP (they guard compiler input boundaries, not output schemas — S3). ACCEPT: matrix rows == S3 census counts; each delete row cites the covering schema pointer.
- [I2 | 15 | I1] Schema-first additions per S3: `minimum: 0` for cost/timeout/backoff numerics, `exclusiveMinimum: 0` verification tier timeout, `minItems` for providers.models/loop.sequence/verification commands — added to `bb.agent_config_surface.v2.schema.json` (this is a constraint tightening of an EXISTING schema, permitted by freeze; log in freeze allowlist). ACCEPT: `pytest -q tests/compilation` green.
- [I3 | 20 | I2] Delete the provably-covered loader checks (S3 consolidation candidates); keep all must_keep rows (extends/cycle detection, version dispatch, pointerful error formatter). Error-message parity: guarded tests (S3 list: test_config_surface_v2, test_config_view_and_tool_registry, test_schema_v2_loader, test_longrun_policy_profile_validation) must stay green UNMODIFIED — if a message regresses, restore the check (matrix decision flips to keep). ACCEPT: full `pytest -q tests/compilation tests/test_schema_v2_loader.py tests/test_longrun_policy_profile_validation.py` green with zero test edits.
- [I4 | 10 | I3] Verifier packet with base-SHA behavior diff (validation outcomes identical pre/post on a corpus of valid+invalid fixture configs — generate 10 invalid fixtures spanning deleted checks). ACCEPT: verdict pass.
FAIL routing: any behavior difference on the fixture corpus → product_defect, restore check, flip matrix row.

### WS-J — Flagship "clone a harness in a day" demo [120 pts]
Intent: §0.5 want #5 — the external falsifier. Candidates (S8): gptme (low-med risk, restricted lane), Aider (medium), Goose (medium), Plandex (med-high). Default: **gptme restricted profile**; fallback order Aider → Goose.
- [J1 | 20 | C1] Predeclared budget doc `docs/plans/phase_20_right_shape/FLAGSHIP_DEMO_PROTOCOL.md`: target + pinned version; bounded behavior profile (enumerate captured behaviors); capture route (adapter); budgets — manifest ≤150 lines, adapter ≤300 lines (both per §4.1 CANONICAL COUNTING), wall-clock ≤ one working day (8h agent-time), predeclared BEFORE work starts; success/failure criteria; the outsider rule — ALLOWED materials (exhaustive): FLAGSHIP_DEMO_PROTOCOL.md itself; `docs/DIRECTION_CHARTER.md`; `docs/authoring/AGENT_CONFIG_AUTHORING.md`; `docs/conformance/E4_COOKBOOK_V2.md`; `bbh` CLI + its help; `scripts/e4_parity/scaffold_e4_target_lane.py`; published schemas under `contracts/kernel/schemas/` + `docs/conformance/schemas/`; the pinned target artifact + that target's OWN public docs/source; everything the outsider itself produces. PROHIBITED: BreadBoard internal implementation files (run_lane/adapter/comparator internals beyond published entry points), existing lane YAMLs, prior campaign evidence/probes/transcripts. Every file access outside the allowed list is logged as a violation with justification. ACCEPT: doc committed BEFORE J2 branch exists (commit order is the evidence).
- [J2 | 40 | J1,G6,A8,H5] Execute the protocol: one implementer subagent plays the outsider under the J1 rules; produces manifest (+lock via bbh), adapter module, captured evidence, accepted-scope claim with exclusions. Timekeeping logged per stage; outsider-rule log kept. ACCEPT: the predeclared protocol was executed HONESTLY AND COMPLETELY (every J1 stage attempted, measurements recorded, no rule violations concealed) and an independent verifier confirms execution+measurement integrity — REGARDLESS of whether budgets/functional criteria passed. Budget/functional pass-fail is recorded as gate G-J (§5), never inside J2's status: J2 done + G-J FAIL is a legitimate, expected-possible state.
- [J3 | 20 | J2] Evidence packet + retro: what the outsider hit (docs gaps, CLI gaps, schema friction), filed as ledger notes + (small) fix packets ≤10 pts each charged to the originating workstream. ACCEPT: retro doc + fixes merged or ticketed with bd ids.
- [J4 | 25 | J2] Thesis measurement vs charter: fill C2 numbers with MEASURED values from J2 (either G-J outcome); if G-J FAILED, the charter states the measured reality (honesty over aspiration) and the gap becomes a named backlog item. ACCEPT: charter updated; measurement table in FLAGSHIP_DEMO_PROTOCOL.md appendix.
- [J5 | 15 | J2..J4] Verifier: independent rerun of the demo lane gates from head SHA; audit of the outsider-rule log for violations. ACCEPT: verdict pass.
FAIL routing (this routes gate G-J, not J2's packet status): demo overruns budget ×2 → STOP the run, record measured costs (J2 still completes as an honest execution), classify which want (#1/#2) failed, route the named gaps as high-priority fix packets, re-run the demo ONCE after fixes; if the rerun also fails its budgets/criteria → G-J is recorded FAIL permanently, J3–J5 proceed on the measured evidence, K2..K4 stay na_gated, and the campaign still completes with the charter stating measured (not aspirational) numbers.

### WS-K — Manifest/Lock rollout decision [core 20 pts; conditional 150]
Gate G-K (§5): F done AND H done AND G-J recorded PASS AND F5(c) churn-locality held for ≥2 weeks of battery runs (or 10 battery runs, whichever first).
- [K1 | 20 | F6,H6,J4,J5] Execute the gate protocol: gather the four condition evidences, write `docs/plans/phase_20_right_shape/GATE_K_DECISION.md` (conditions, evidence refs, PASS/FAIL, rationale), update ledger gates[]. ACCEPT: decision doc with all four conditions evidenced; verifier countersigns.
- CONDITIONAL (unlocked only on PASS; 150 pts in conditional track): K2 migrate remaining 20 lanes to manifest+lock (batched 5×4, battery green per batch, 100 pts); K3 retire legacy lane_def loader path + refresh legacy mode (30); K4 pin-graph audit proving global acyclicity manifest←lock←evidence (20).

### WS-L — Evidence-core extraction decision [core 10 pts; conditional 100]
Gate G-L (§5): G-K PASSED AND (a second external consumer exists for the evidence machinery OR the demo retro J3 explicitly identifies reuse demand).
- [L1 | 10 | K1] Same protocol as K1 → `GATE_L_DECISION.md`. ACCEPT: decision doc + countersign. Expected outcome at plan time: FAIL/defer (no second consumer today) — that is a legitimate, points-earning outcome; the doc must say what evidence would flip it.
- CONDITIONAL (100 pts): L2 extract claim/C4/comparator/fixed-point core to `libs/evidence_core/` with repo adapters (70); L3 migrate scripts to consume it (30).

### WS-M — Campaign closeout [10 pts]
Intent: §6 criteria 11–12 are executable packets, not afterthoughts.
- [M1 | 10 | A8,B3,C4,D5,E5,F6,G6,H6,I4,J5,K1,L1] Closeout: (a) every bd child issue closed or blocked-with-classification; (b) final `bd dolt push` clean (output logged); (c) final derailment audit by a fresh reviewer subagent — zero §0.3 violations across the campaign diff; (d) `docs/plans/phase_20_right_shape/SPEC_AMENDMENTS.md` finalized (every deviation, or the standing "none" statement); (e) final completeness critic (fresh subagent) confirms §6 criteria 2–12 with evidence refs, and §6.1 over every item EXCEPT M1 itself. ACCEPT: all five recorded in ledger evidence; verifier countersigns the critic's report — that countersign is the campaign's final act and closes §6.1 for M1 (no cycle: M1 is never asked to pre-verify its own completion).
FAIL routing: any §6 criterion unmet → reopen the owning item as product_defect/spec_gap; M1 re-runs after the fix (M1 is the ONLY item allowed to reopen others).

---

## 5. Gate table

| Gate | Condition (ALL required) | Evidence | On PASS | On FAIL |
|---|---|---|---|---|
| G-D (product spine live) | D1..D5 done; spine ≤10 min; green on PR | run URL + duration | unlocks "CI-proven" claims in charter | fix or move member to battery; re-run |
| G-F (pilot proven) | F4 round-trip equality; F5 a/b/c green | logged cmds + SHAs | H5/J2 may proceed on migrated source | route per WS-F FAIL table |
| G-J (falsifier) | J2 executed honestly AND all J1 functional criteria met AND within 1.0× J1 budgets (single threshold; no ×1.5 variant) | timekeeping + lane green through run_lane + claim reverify | charter numbers = measured; G-K eligible | WS-J FAIL routing (one rerun max, then permanent FAIL) |
| G-K (rollout) | see WS-K | GATE_K_DECISION.md | conditional K2..K4 unlock (+150 track) | K2..K4 stay na_gated; revisit criteria named in decision doc |
| G-L (extraction) | see WS-L | GATE_L_DECISION.md | conditional L2..L3 unlock (+100 track) | stays na_gated; flip-evidence named |

## 6. Completion criteria (ALL must hold; checked by a final completeness critic + human-visible summary)
1. Ledger `points_done == 1000` with every CORE item and every gate-UNLOCKED conditional item carrying evidence refs + independent verifier verdict(s) bound to head SHAs (§1.2). Evaluation order: the M1 final critic verifies this criterion over all items EXCEPT M1; M1's own verifier countersign (the campaign's final act) then closes it — M1 never pre-verifies itself. Gated-off conditional items (`na_gated`) carry NO packet evidence/verdict — their required record is the gate decision doc reference (§5) only. Conditional track reported separately (0, 150, or 250 as gates dictated) — completion does NOT require conditional points, only recorded gate DECISIONS.
2. Both SDKs drive the README flow against default `create_app()` in CI (green run URL recorded).
3. `product-spine` required on merge — evidenced by API/settings output, or by D5's human-signed WAIVER in SPEC_AMENDMENTS.md (missing credential + exact activation command); battery live with ≥1 green PR-triggered run (D3) AND ≥1 green nightly/dispatch run (D2).
4. `docs/DIRECTION_CHARTER.md` + `FREEZE_POLICY.md` + tier registry committed; packs.v1.json no longer claims "minimal expressive harness primitive language"; zero kept-tier schemas without named consumers.
5. P6.6 lane runs green from manifest+lock+sidecar; churn probe holds; refresh cannot touch manifests.
6. Zero dishonest stage outcomes repo-wide: every lane source declares stage modes; `check_stage_report` enforces the §4.4 table for every stage of every run; battery green.
7. Validation gap matrix committed; consolidation applied with guarded error-message tests green UNMODIFIED.
8. `bbh` front door installed and documented; E2E verifier transcript on record.
9. Flagship demo executed per predeclared protocol; charter carries MEASURED numbers; retro fixes merged/ticketed.
10. GATE_K/GATE_L decision docs exist with countersigned verdicts (whatever the verdicts are).
11. All bd child issues closed or blocked-with-classification; `bd dolt push` clean; final derailment audit finds zero §0.3 violations.
12. `SPEC_AMENDMENTS.md` contains every deviation from this plan (or states "none"); no undocumented deviations exist (final critic greps for drift).

---

## 4. Literal specifications (authoritative; authored by the orchestrator — implement EXACTLY, amend only via SPEC_AMENDMENTS.md)

### 4.1 `bb.e4.lane_manifest.v1` — author-owned E4 lane intent (JSON Schema, commit verbatim)
Naming note: this is the CONFORMANCE-lane intent document. The product harness configuration is and remains `bb.agent_config_surface.v2` — the two must never be conflated (charter boundary). A lane manifest that proves a BreadBoard-authored harness references its agent config via `agent_config_ref`.
Design rules: no digest may ever appear in a manifest (locks own digests); every field is human-authored intent; loader (`validate_lane.py`) additionally enforces (a) document contains no `sha256:` substring anywhere, (b) warn >150 canonical lines, hard error >300. CANONICAL COUNTING (anti-gaming, enforced by the loader): manifests MUST be block-style YAML with max line length 120 (loader errors on flow-style collections spanning >1 key or any line >120 chars); the count is nonblank, non-comment lines of the committed file. ADAPTER LOC = nonblank, non-comment lines summed over the TRANSITIVE set of new target-specific source files reachable from the registered adapter entrypoint (imports of pre-existing shared modules excluded; new helper modules count). The demo gate (J1/G-J) reports BOTH counts computed from the committed tree by script. Budget semantics: the THESIS number (charter, demo J1) is ≤150 lines hard for the flagship demo lane; the ≤300 hard ceiling exists only so the P6.6 pilot (densest legacy lane) can migrate without gaming — the pilot does not get thesis credit.
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "bb.e4.lane_manifest.v1",
  "title": "BreadBoard E4 lane manifest (author-owned conformance intent; digests live in bb.e4.lane_lock.v1)",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "lane_id", "config_id", "target", "kind", "capture", "normalize", "replay", "compare", "claim", "artifacts_root"],
  "properties": {
    "schema_version": { "const": "bb.e4.lane_manifest.v1" },
    "lane_id": { "type": "string", "pattern": "^[a-z0-9][a-z0-9_-]*$" },
    "config_id": { "type": "string", "pattern": "^[a-z0-9][a-z0-9_.-]*$" },
    "title": { "type": "string", "maxLength": 200 },
    "agent_config_ref": { "type": ["string", "null"], "$comment": "repo-relative path to the bb.agent_config_surface.v2 document exercised by this lane, when the lane proves a BreadBoard-authored harness; null for external targets" },
    "kind": { "enum": ["target_support", "self_runtime", "probe"] },
    "status": { "enum": ["draft", "candidate", "accepted"] },
    "points": { "type": "integer", "minimum": 0 },
    "target": {
      "type": "object", "additionalProperties": false, "required": ["family", "version"],
      "properties": {
        "family": { "type": "string", "pattern": "^[a-z0-9][a-z0-9_-]*$" },
        "version": { "type": "string", "minLength": 1 },
        "package_ref": { "type": ["string", "null"] },
        "source_freeze_ref": { "type": ["string", "null"], "$comment": "repo-relative path only; its digest is resolved into the lock" }
      }
    },
    "capture": {
      "type": "object", "additionalProperties": false, "required": ["strategy"],
      "properties": {
        "strategy": { "enum": ["adapter", "probe_argv", "replay_dump", "runtime_records"] },
        "adapter": { "type": ["string", "null"], "pattern": "^[a-z0-9][a-z0-9_]*$" },
        "argv": { "type": ["array", "null"], "items": { "type": "string" } },
        "inputs": { "type": "array", "items": { "type": "string" }, "$comment": "repo-relative paths; digests resolved into lock.resolved_inputs" },
        "workspace_template": { "type": ["string", "null"] }
      },
      "allOf": [
        { "if": { "properties": { "strategy": { "const": "adapter" } } }, "then": { "required": ["adapter"], "properties": { "adapter": { "type": "string" } } } },
        { "if": { "properties": { "strategy": { "const": "probe_argv" } } }, "then": { "required": ["argv"], "properties": { "argv": { "type": "array" } } } }
      ]
    },
    "normalize": {
      "type": "object", "additionalProperties": false, "required": ["mode"],
      "properties": {
        "mode": { "enum": ["identity", "translate"] },
        "translator": { "type": ["string", "null"] },
        "record_builders": { "type": "array", "items": { "type": "object" } },
        "projection_constants": { "type": "object" },
        "required_records": { "type": "array", "items": { "type": "string" } },
        "required_roles": { "type": "array", "items": { "type": "string" } }
      },
      "allOf": [ { "if": { "properties": { "mode": { "const": "translate" } } }, "then": { "required": ["translator"] } } ]
    },
    "replay": {
      "type": "object", "additionalProperties": false, "required": ["mode", "comparator_class"],
      "properties": { "mode": { "enum": ["stored"], "$comment": "Phase 20 scope: stored replay ONLY. No executable replay provider exists in the tree (verified at plan time); adding an 'executed' mode is a post-campaign schema relaxation gated on a real provider + tests, via SPEC_AMENDMENTS.md." }, "comparator_class": { "type": "string" } }
    },
    "compare": {
      "type": "object", "additionalProperties": false, "required": ["comparator"],
      "properties": {
        "comparator": { "type": "string" },
        "assertions": { "type": "array", "items": {
          "type": "object", "additionalProperties": false, "required": ["assertion_id", "kind", "description"],
          "properties": {
            "assertion_id": { "type": "string" }, "kind": { "type": "string" }, "description": { "type": "string" },
            "record_selector": { "type": "object" }, "expect": {}
          } } }
      }
    },
    "claim": {
      "type": "object", "additionalProperties": false, "required": ["scope", "exclusions"],
      "properties": {
        "scope": { "type": "object", "additionalProperties": false, "required": ["behaviors"],
          "properties": { "behaviors": { "type": "array", "minItems": 1, "items": { "type": "string" } },
                          "surfaces": { "type": "array", "items": { "type": "string" } } } },
        "exclusions": { "type": "array", "items": {
          "type": "object", "additionalProperties": false, "required": ["id", "reason"],
          "properties": { "id": { "type": "string" }, "reason": { "type": "string" } } } }
      }
    },
    "ct": { "type": "object", "additionalProperties": false,
      "properties": { "description": { "type": "string" }, "timeout_seconds": { "type": "integer", "minimum": 1 } } },
    "acceptance": { "type": "object", "additionalProperties": false,
      "properties": { "behavior_families": { "type": "array", "items": { "type": "string" } }, "notes": { "type": "string" } } },
    "reverify_command": { "type": "object", "additionalProperties": false, "required": ["argv"],
      "properties": { "argv": { "type": "array", "minItems": 1, "items": { "type": "string" } }, "cwd": { "type": "string" } } },
    "artifacts_root": { "type": "string" },
    "notes": { "type": "string" }
  }
}
```
Migration note: field names deliberately mirror `bb.e4.lane_def.v2` author_intent pointers (SCOUT_FACTS S2) so `lane_definitions.py` can normalize (manifest+lock+sidecar) into the SAME lane_def dict the runtime consumes. If the pilot surfaces a missing intent field, add it via SPEC_AMENDMENTS.md with its S2 pointer cited.

### 4.2 `bb.e4.lane_lock.v1` — machine-owned deterministic lane resolution (JSON Schema, commit verbatim)
THREE-LAYER RULE: the lock is a pure function of (manifest bytes, referenced file bytes, registry entries). Identical inputs MUST produce byte-identical locks. Therefore: NO timestamps, NO durations, NO run/stage results, NO environment data. Canonical serialization: JSON, UTF-8, sorted keys, separators `(",", ":")`, `ensure_ascii=false`, single trailing newline.
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "bb.e4.lane_lock.v1",
  "title": "BreadBoard E4 lane lock (machine-owned deterministic resolution; never hand-edited)",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "lock_format", "lane_id", "manifest_ref", "manifest_sha256", "target_freeze", "resolved_inputs", "artifact_roles", "packet_constants_ref", "registry_pins"],
  "properties": {
    "schema_version": { "const": "bb.e4.lane_lock.v1" },
    "lock_format": { "const": "canonical-json-v1" },
    "lane_id": { "type": "string", "pattern": "^[a-z0-9][a-z0-9_-]*$" },
    "manifest_ref": { "type": "string" },
    "manifest_sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" },
    "target_freeze": {
      "type": "object", "additionalProperties": false, "required": ["config_id", "freeze_manifest_row_sha256"],
      "properties": { "config_id": { "type": "string" },
                      "freeze_manifest_row_sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" } }
    },
    "resolved_inputs": { "type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["path", "sha256", "bytes", "role"],
      "properties": { "path": { "type": "string" }, "sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" },
                      "bytes": { "type": "integer", "minimum": 0 }, "role": { "type": "string" } } } },
    "artifact_roles": {
      "type": "object",
      "patternProperties": { "^[a-z0-9][a-z0-9_]*$": {
        "type": "object", "additionalProperties": false, "required": ["path"],
        "properties": { "path": { "type": "string" },
                        "sha256": { "type": ["string", "null"], "pattern": "^sha256:[0-9a-f]{64}$" },
                        "bytes": { "type": ["integer", "null"], "minimum": 0 } } } },
      "additionalProperties": false
    },
    "packet_constants_ref": {
      "type": ["object", "null"], "additionalProperties": false, "required": ["path", "sha256"],
      "properties": { "path": { "type": "string" }, "sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" } },
      "$comment": "generated sidecar holding payload_templates/substitutions extracted from legacy lane defs; machine-owned"
    },
    "registry_pins": { "type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["registry", "entry_id", "entry_sha256"],
      "properties": { "registry": { "type": "string" }, "entry_id": { "type": "string" },
                      "entry_sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" } } } }
  }
}
```
Run/evidence layer (third layer) is NOT a new schema family: stage reports (§4.4) live inside `run_lane`'s existing lane-run report JSON; claims/evidence manifests/C4 chains are the existing E4 records. Each run report records `lock_sha256` — the chain is `manifest ← lock ← run/evidence`, never a reference in the other direction.

### 4.3 `bb.contract_tiers.v1` — tier registry (JSON Schema, commit verbatim)
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "bb.contract_tiers.v1",
  "title": "Consumer-backed tier registry for the published contract estate",
  "type": "object", "additionalProperties": false,
  "required": ["schema_version", "registry_id", "baseline_sha", "tiers", "entries"],
  "properties": {
    "schema_version": { "const": "bb.contract_tiers.v1" },
    "registry_id": { "const": "contract_tiers" },
    "baseline_sha": { "type": "string", "$comment": "repo commit the census was taken against" },
    "tiers": { "const": ["runtime_protocol", "config_algebra", "host_protocol", "evidence", "frozen_legacy"] },
    "entries": { "type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["schema_id", "tier", "consumers", "disposition"],
      "properties": {
        "schema_id": { "type": "string", "pattern": "^bb\\.[a-z0-9_.]+\\.v[0-9]+$" },
        "tier": { "enum": ["runtime_protocol", "config_algebra", "host_protocol", "evidence", "frozen_legacy"] },
        "consumers": { "type": "array", "items": {
          "type": "object", "additionalProperties": false, "required": ["kind", "path"],
          "properties": { "kind": { "enum": ["runtime_emission", "loader", "api", "sdk", "evidence_machinery", "tests"] },
                          "path": { "type": "string" } } } },
        "disposition": { "enum": ["keep", "freeze", "supersede"] },
        "superseded_by": { "type": ["string", "null"] },
        "notes": { "type": "string" }
      } } }
  }
}
```
Invariant (enforced by a check in the freeze script): `disposition == "keep"` requires `consumers.length >= 1`. Seeding: tiers for the 49 non-runtime kernel entries come from SCOUT_FACTS S5a (31 host_protocol, 9 config_algebra, 8 evidence, 1 supersede); the census itself is GENERATED from packs.v1.json per E1 (61 kernel + 13 E4 + 3 program + 14 legacy_frozen, +3 new = 94 expected; generated set is authoritative). The 3 program schemas get explicit tier+disposition during E1; they are in the estate and may not be omitted.

### 4.4 Stage plugin contracts — `scripts/e4_parity/stage_contracts.py` (commit the declarations verbatim; `check_stage_report` body is implemented in WS-H H1 exactly per its docstring rules; NOT a new bb.* schema family)
```python
"""Stage contracts for honest lane execution (Phase 20 WS-H).

Migration-compatible with the live adapter signature (SCOUT_FACTS S7):
    capture(lane_def, inventory_lane, *, promote_accepted=..., out_dir=...)
Legacy comparators exposing ``compare(*args, **kwargs)`` are wrapped by run_lane
with a keyword-forwarding shim; do NOT rewrite adapters in bulk (blast radius).
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict


class ReusedInput(TypedDict):
    path: str        # repo-relative path of the stored artifact being reused
    sha256: str      # ^sha256:[0-9a-f]{64}$ — digest at reuse time (provenance)


class StageReport(TypedDict):
    stage: Literal["capture", "normalize", "replay", "compare", "claim"]
    outcome: Literal[
        "executed_pass",          # stage ran; gates green; report_ref REQUIRED
        "executed_fail",          # stage ran (or should have run) and did not pass; detail REQUIRED
        "reused_stored_result",   # author-declared reuse of checked-in artifacts; manifest_rule + reused_inputs REQUIRED
        "disabled_by_manifest",   # author-declared off for this lane; manifest_rule REQUIRED
        "not_applicable",         # lane kind structurally lacks this stage; manifest_rule points at /kind
    ]
    manifest_rule: str | None     # JSON Pointer into the lane manifest authorizing reuse/disable/NA (e.g. "/replay/mode")
    reused_inputs: list[ReusedInput] | None   # provenance digests for reused_stored_result
    report_ref: str | None        # repo-relative stage artifact; REQUIRED for executed_*
    lock_sha256: str | None       # lane-lock digest this run executed against; None only for unmigrated legacy lanes
    detail: str                   # human-readable summary; REQUIRED non-empty for executed_fail


def check_stage_report(report: StageReport, manifest: Mapping[str, Any]) -> list[str]:
    """Generic, table-driven honesty check — the ONLY validator for these rules.

    Returns error strings; empty means honest. Rules:
      1. executed_pass/executed_fail  -> report_ref required (fail: detail required too).
      2. reused_stored_result        -> manifest_rule must RESOLVE in the manifest to one of:
                                        /replay/mode == "stored", /capture/strategy in
                                        {"replay_dump","runtime_records"};
                                        reused_inputs must be non-empty with valid digests.
                                        (normalize NEVER reuses: identity mode is an EXECUTED
                                        no-op translator emitting executed_pass — see WS-H H2.)
      3. disabled_by_manifest        -> manifest_rule must resolve and its value must be false/"off".
      4. not_applicable              -> manifest_rule must be "/kind" and the lane kind's stage table
                                        (STAGES_BY_KIND below) must omit this stage.
      5. Anything else is dishonest: report it.
    """
    ...


STAGES_BY_KIND: dict[str, tuple[str, ...]] = {
    "target_support": ("capture", "normalize", "replay", "compare", "claim"),
    "self_runtime": ("capture", "normalize", "replay", "compare", "claim"),
    "probe": ("capture", "claim"),
}


class CaptureAdapter(Protocol):
    def capture(
        self,
        lane_def: Mapping[str, Any],
        inventory_lane: Mapping[str, Any] | None = None,
        *,
        promote_accepted: bool = False,
        out_dir: Path | None = None,
    ) -> Mapping[str, Any]: ...


class Translator(Protocol):
    def translate(self, payload: Any, *, config: Mapping[str, Any] | None = None) -> Any: ...


class ReplayProvider(Protocol):
    # DEFERRED (Phase 20 scope: stored replay ONLY). No replay provider,
    # registry, or executed-replay path exists in the tree (verified at plan
    # time). This Protocol is committed as the reserved seam ONLY; nothing may
    # implement or dispatch to it this campaign. Introducing executed replay
    # requires a SPEC_AMENDMENTS.md entry with a concrete provider + tests.
    def replay(
        self,
        *,
        lane_def: Mapping[str, Any],
        capture_report: Mapping[str, Any],
        out_dir: Path,
    ) -> Mapping[str, Any]: ...


class Comparator(Protocol):
    # GROUNDED IN THE LIVE SYSTEM (do not redesign): the governed registry
    # `conformance/comparators/registry.json` maps to entrypoints
    # `conformance.comparators.<module>:compare`, whose contract is defined by
    # `conformance/comparators/protocol.py` (ComparatorInput): ONE mapping
    # containing decoded `capture`, `replay`, `scope`, and the artifact-path
    # map for every declared input_role; returns the comparator report mapping.
    # run_lane (WS-H H4) constructs that ComparatorInput exactly per
    # protocol.py, invokes the registry entrypoint, validates and writes the
    # returned report. Adapter wrappers exposing legacy `compare(*args,
    # **kwargs)` are wrapped by a keyword-forwarding shim in run_lane.
    def compare(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...
```
The honesty semantics live in the DATA (resolvable pointers, digests) checked by ONE table-driven checker — not scattered hand-written validators. `check_stage_report` is implemented in WS-H H1 exactly per its docstring; run_lane calls it for every stage of every run and fails the run on any non-empty result.

### 4.5 CLI surface — `bbh` (BreadBoard front door; namespaces mirror the charter boundary)
Console script `bbh = scripts.breadboard_cli:main` via new minimal root `pyproject.toml`. (`breadboard` bin name is owned by the TUI package — do not take it.) `bbh harness ...` operates on PRODUCT harness configs (`bb.agent_config_surface.v2`); `bbh lane ...` operates on E4 CONFORMANCE lane manifests (`bb.e4.lane_manifest.v1`). Global flags: `--json`, `--quiet`. Exit codes: 0 ok · 2 validation failure · 3 resolution/reference failure · 4 runtime/session failure · 5 lock drift.

| Command | Delegates to | Behavior |
|---|---|---|
| `bbh harness init [--out DIR]` | copies `agent_configs/templates/minimal_harness.v2.yaml` (+ prompts) | never overwrites; prints next-step hint |
| `bbh harness validate PATH` | v2 loader validation (`bb.agent_config_surface.v2`) | pointerful errors, exit 2 on invalid |
| `bbh harness explain PATH [--strict]` | `scripts/authoring/explain_agent_config.py` | unchanged output; exit mirrors script |
| `bbh harness run PATH [--server URL \| --local] [--task TEXT]` | breadboard_sdk /v1 flow: create session → input → poll records → stream events; `--local` boots `create_app()` in-process first | prints session id + record count; exit 4 on session failure |
| `bbh lane init [--out DIR]` | writes a §4.1 lane-manifest skeleton | never overwrites |
| `bbh lane validate PATH` | §4.1 schema + no-sha256 + line-budget checks (`validate_lane.py` core) | exit 2 on invalid |
| `bbh lane lock PATH [--check] [--out DIR]` | `scripts/e4_parity/compile_lane_lock.py` | `--check` exit 5 on byte drift |
| `bbh lane capture MANIFEST [--out DIR]` | `run_lane.py` capture stage for migrated lanes | scratch out_dir default under docs_tmp |

### 4.6 SDK `/v1` repair map (from SCOUT_FACTS S1; the OpenAPI document is the truth — no server edits)
| Client call site | Old path | New path |
|---|---|---|
| ts client.ts:104-111 sessions CRUD/input/command | `/sessions...` | `/v1/sessions...` |
| ts client.ts:113 listSessionFiles | `/sessions/{id}/files` | `/v1/sessions/{id}/files` |
| ts client.ts:117 readSessionFile | `/sessions/{id}/files` | `/v1/sessions/{id}/files/content` (suffix change) |
| ts client.ts:127 models | `/models` | `/v1/models` |
| ts client.ts:128-129 skills/ctrees | `/sessions/{id}/...` | `/v1/sessions/{id}/...` |
| ts stream.ts:73/87 SSE | `/sessions/{id}/events` | `/v1/sessions/{id}/events` |
| py client.py sessions/models/attachments/stream | `/sessions...`, `/models` | `/v1/...` same rules; multipart + stream URLs included |
| py client.py read_session_file | `/sessions/{id}/files` | `/v1/sessions/{id}/files/content` |
| BOTH `/health` | `/health` | UNCHANGED (intentionally unversioned) |
| BOTH provider-auth | `/v1/provider-auth/*` | already correct — do not touch |

SSE envelope (do not change parsing): `id: <seq>\ndata: <JSON>\n\n`, no `event:` field; JSON fields id/type/session_id/turn/timestamp/payload (+optional timestamp_ms/seq/run_id/thread_id).

### 4.7 Direction charter — `docs/DIRECTION_CHARTER.md` (commit verbatim; C2/J4 may update ONLY the two bracketed numbers)
```markdown
# BreadBoard Direction Charter

BreadBoard is a maximalist engine for agent-harness design: one engine truth surface expressive
enough to build any modern harness and to clone existing harnesses as configuration plus a bounded
adapter, with evidence that names exactly what is proven and what is excluded.

**The product** is the runtime engine, the v2 config surface and compiler, the /v1 session API, and
the SDK/TUI/CLI clients over it. **E4 is the internal conformance backend** that makes our parity
claims trustworthy; its records, lanes, catalogs, ledgers, and regeneration machinery are not part
of the ordinary harness-builder experience and are not public API.

**Falsifiable thesis.** An outsider, using only published docs and the `bbh` front door, can clone a
bounded harness profile in one working day: hand-authored manifest ≤ [150] lines and adapter code
≤ [300] lines, producing a session-running harness and an accepted-scope conformance claim.
The flagship demo protocol (docs/plans/phase_20_right_shape/FLAGSHIP_DEMO_PROTOCOL.md) measures this;
measured numbers replace aspirations here after each run.

**Contract tiers.** Published schemas carry tiers in contracts/kernel/registries/contract_tiers.v1.json:
runtime_protocol / config_algebra / host_protocol / evidence / frozen_legacy. "Minimal" refers to the
runtime_protocol tier only. A kept schema names a real consumer or it is frozen.

**Layering.** Product harness configs are bb.agent_config_surface.v2 documents. E4 lane intent lives
in lane manifests (bb.e4.lane_manifest.v1); deterministic resolution lives in lane locks
(bb.e4.lane_lock.v1) and generated sidecars, machine-owned, never hand-edited.
Run evidence references locks by digest. The chain is manifest ← lock ← evidence, never reversed.
Every stage reports an explicit outcome (executed, reused-with-provenance, disabled-by-manifest, or structurally not-applicable); silent skips are defects.

**Change discipline.** Surface growth (schema families, SDK packages, lane kinds, ledgers,
scorecards) is frozen except through docs/plans/phase_20_right_shape/FREEZE_POLICY.md. External-target
capture MAY require adapter code; unbounded descriptors and replicated hash pins may not return.
The current campaign and its completion criteria: docs/plans/phase_20_right_shape/BB_RS_MASTER_PLAN.md.
```

### 4.8 Progress ledger — `docs/plans/phase_20_right_shape/BB_RS_PROGRESS.json` (shape; seeded at plan commit)
```json
{
  "schema_version": "bb.rs_progress.v1",
  "campaign": "phase_20_right_shape",
  "plan_ref": "docs/plans/phase_20_right_shape/BB_RS_MASTER_PLAN.md",
  "points_total": 1000,
  "points_done": 0,
  "conditional_total": 250,
  "conditional_done": 0,
  "updated_at_utc": "...",
  "items": [ { "id": "A1", "ws": "A", "title": "...", "weight": 15, "status": "todo|in_progress|done|blocked|na_gated",
               "deps": ["..."], "evidence": [ { "kind": "command|commit|ci_run|doc", "ref": "...", "sha": "..." } ],
               "verifiers": [ { "agent": "...", "lens": "correctness|anti_scope", "verdict": "pass|fail", "verdict_ref": "..." } ], "notes": "" } ],
  "gates": [ { "id": "G-K", "status": "pending|pass|fail", "decision_ref": "" } ],
  "amendments": []
}
```
This file is data, not a schema family; `bb.rs_progress.v1` is a local discriminator string, not a kernel schema (freeze-compatible). Verifier rule (§1.3): items with weight ≥30 require ≥2 passing entries in `verifiers` with DIFFERENT lenses (correctness; anti_scope); all other done items require ≥1. `na_gated` items keep `verifiers: []` (§6.1).

### 4.9 CI additions (sketch; exact YAML authored in WS-D packets)
`product-spine` job (PR + push, required): checkout → setup python/node (cached) → the six D1 commands → A7 smokes (D5). Budget ≤10 min.
`.github/workflows/e4-battery.yml`: `schedule: cron "0 8 * * *"` + `workflow_dispatch` + PR `paths:` filter (`scripts/e4_parity/**`, `tests/e4_parity/**`, `contracts/kernel/**`, `config/e4_lanes/**`) → full `pytest -q tests/e4_parity` + `regen.py fixed-point --json`.

### 4.10 Freeze policy — `docs/plans/phase_20_right_shape/FREEZE_POLICY.md` (commit verbatim)
```markdown
# Phase 20 Surface Freeze

Effective from the commit introducing this file until the Phase 20 completion criteria pass
(BB_RS_MASTER_PLAN.md §6), the following surfaces are FROZEN:
- New schema families under contracts/kernel/schemas/ — except bb.e4.lane_manifest.v1,
  bb.e4.lane_lock.v1, bb.contract_tiers.v1 (the three Phase 20 schemas). Constraint tightening of
  existing schemas is permitted when a plan packet requires it (each occurrence allowlisted in
  scripts/check_phase20_freeze.py with the packet id).
- New SDK packages (sdk/*/package.json count is fixed).
- New lane kinds and new lanes under config/e4_lanes/ (the flagship demo lane is allowlisted).
- New ledgers, scorecards, campaign evidence snapshot forms (BB_RS_PROGRESS.json is the only
  campaign scoreboard).
Unfreeze requires ONE of: (a) a failing flagship-demo or consumer case that names the missing
surface; (b) a demonstrated correctness defect fixable only by a new surface; (c) explicit user
sign-off recorded in docs/plans/phase_20_right_shape/SPEC_AMENDMENTS.md. Every unfreeze is an
amendment entry: what, why, evidence, scope.
Enforcement: scripts/check_phase20_freeze.py runs in the product-spine CI job and fails the build
on violations. The baseline SHA is recorded in the script.
```

---

## 7. Provenance and companion files
- `SCOUT_FACTS.json` (this dir): structured outputs of the 8 precision scouts (S1..S8) this plan binds to. Treat as read-only input; re-verify any fact that looks stale against the tree before relying on it.
- `JUDGE_BRIEF_CORRECTED_20260710.md` (this dir): the corrected findings brief the judge panel reviewed.
- `SPEC_AMENDMENTS.md` (this dir; created on first amendment): every deviation from this plan, dated, with evidence. An empty campaign ends with this file stating "none".
- Plan authored 2026-07-10 by the orchestrating agent (Claude) from the direction assessment; execution designed for GPT 5.6 under Oh-My-Pi with eval-workflow subagents (§1.3).
