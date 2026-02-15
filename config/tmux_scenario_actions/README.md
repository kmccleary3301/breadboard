# tmux Scenario Actions (Canonical, Committed)

This directory is the canonical, git-tracked home for tmux scenario action JSON files consumed by:

- `scripts/run_tmux_capture_scenario.py`
- CI soft gates (for example `.github/workflows/tmux-e2e-soft-gate.yml`)

## Structure

- `ci/`: deterministic self-check scenarios (synthetic panes; no network/providers).
- `phase4_replay/`: deterministic replay-in-tmux scenarios used for Phase 4 closeout verification.
- Top-level `*_e2e_*.json`: provider-driven scenarios (typically nightly/report-only; do not make PR CI flaky).

## What Does Not Belong Here

- Captured run artifacts (frames, PNGs, manifests) are output-only and should go under `docs_tmp/` (gitignored).
- tmux "goldens" are treated as local/ops artifacts under `docs_tmp/tmux_captures/goldens` (not git-tracked) and are managed via
  `scripts/bless_tmux_golden.py`.

## Conventions

- Always target `breadboard_test_*` sessions in automation (default safety posture of the runner).
- Include an initial `wait_until` ready marker (for example `for shortcuts`) before sending input.
- Prefer `must_contain`/`must_match_regex` semantic assertions over brittle full-screen text matching.

