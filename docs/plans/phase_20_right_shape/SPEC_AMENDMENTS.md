# Phase 20 Spec Amendments

Every deviation from BB_RS_MASTER_PLAN.md is recorded here, dated, with evidence (§1.5 spec_gap protocol).

Current state: **3 amendments** (below).

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
