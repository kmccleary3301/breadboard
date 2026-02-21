# tmux Scenario Actions (Canonical, Committed)

This directory is the canonical, git-tracked home for tmux scenario action JSON files consumed by:

- `scripts/run_tmux_capture_scenario.py`
- CI soft gates (for example `.github/workflows/tmux-e2e-soft-gate.yml`)

## Structure

- `ci/`: deterministic self-check scenarios (synthetic panes; no network/providers).
  - `ci/phase4_replay/*`: deterministic replay-in-tmux scenarios used by phase4 replay checks.
- `nightly_provider/`: provider-driven scenarios (nightly/report-only; do not make PR CI flaky).
- `nightly_stress/`: deterministic stress/resize scenarios (nightly/report-only).

Compatibility (legacy paths kept temporarily):
- `phase4_replay/*` and top-level `*_e2e_*.json` are legacy aliases.
- New work should only add/update files under `ci/*` or `nightly_provider/*`.
- New nightly stress scenarios should live under `nightly_stress/*`.

## What Does Not Belong Here

- Captured run artifacts (frames, PNGs, manifests) are output-only and should go under `docs_tmp/` (gitignored).
- tmux "goldens" are treated as local/ops artifacts under `docs_tmp/tmux_captures/goldens` (not git-tracked) and are managed via
  `scripts/bless_tmux_golden.py`.

## Conventions

- Always target `breadboard_test_*` sessions in automation (default safety posture of the runner).
- Include an initial `wait_until` ready marker (for example `for shortcuts`) before sending input.
- Prefer `must_contain`/`must_match_regex` semantic assertions over brittle full-screen text matching.
