# Phase 20 Spec Amendments

Every deviation from BB_RS_MASTER_PLAN.md is recorded here, dated, with evidence (§1.5 spec_gap protocol).

Current state: **7 amendments** (below).

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
