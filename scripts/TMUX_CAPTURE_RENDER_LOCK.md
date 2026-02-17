# tmux Capture Render Lock

This capture stack is now pinned to a versioned render profile:

- profile id: `phase4_locked_v1`
- source of truth: `scripts/tmux_capture_render_profile.py`
- default in capture pipeline:
  - `scripts/run_tmux_capture_scenario.py`
  - `scripts/tmux_capture_poll.py`
  - `scripts/tmux_capture_to_png.py`

Locked defaults:

- renderer: `pillow`
- bg: `1f2430`
- fg: `f1f5f9`
- font size: `18`
- scale: `1.0`
- cell width/height: auto (`0/0`)
- line gap: `0`
- padding: `0/0`

Replay target defaults are also pinned for visual consistency:

- `scripts/start_tmux_phase4_replay_target.sh` defaults:
  - `--scrollback-mode scrollback`
  - `--landing-always 1`
- `scripts/phase4_replay_target_entrypoint.sh` uses the same defaults.

Rules:

1. Do not change `phase4_locked_v1` values in-place.
2. If render behavior must change, add a new profile id (e.g. `phase4_locked_v2`) and migrate intentionally.
3. Capture artifacts should record `render_profile` in `meta.json` and `scenario_manifest.json` for traceability.
