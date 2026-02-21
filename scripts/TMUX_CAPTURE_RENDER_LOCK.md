# tmux Capture Render Lock

This capture stack is now pinned to versioned render profiles:

- capture/replay default profile id: `phase4_locked_v5`
- ground-truth evaluator default profile id: `phase4_locked_v5`
- source of truth: `scripts/tmux_capture_render_profile.py`
- default in capture pipeline:
  - `scripts/run_tmux_capture_scenario.py`
  - `scripts/tmux_capture_poll.py`
  - `scripts/tmux_capture_to_png.py`

Locked defaults:

- renderer: `pillow`
- bg: `1f2430`
- fg: `f1f5f9`
- font size: `14`
- scale: `1.0`
- locked fonts: `renderer_assets/fonts/Consolas*.ttf` (checksum-verified)
- cell width/height: `9/22`
- line gap: `0`
- padding: `0/1`
- baseline offset: `17`
- underline offset: `2`
- height mode default: `pane` (fixed viewport height)
- glyph provenance default: `interesting` sidecar

Freeze manifest and checker:

- freeze manifest: `config/render_profile_freeze_manifest.json`
- checker script: `scripts/check_render_profile_freeze.py`
- CI gate workflow: `.github/workflows/render-profile-freeze-gate.yml`
- required job context name: `render profile freeze gate (strict)`

Replay target defaults are also pinned for visual consistency:

- `scripts/start_tmux_phase4_replay_target.sh` defaults:
  - `--scrollback-mode scrollback`
  - `--landing-always 1`
- `scripts/phase4_replay_target_entrypoint.sh` uses the same defaults.
- `scripts/run_tmux_capture_scenario.py` default:
  - `--capture-mode fullpane`
  - `--fullpane-start-markers "BreadBoard v,No conversation yet"`

Footer/input-bar contract is now pinned in run validation:

- validator: `scripts/validate_phase4_footer_contract.py`
- enforced row shape in final frame:
  - border row
  - prompt row (`❯ Try "fix typecheck errors"`)
  - border row
  - shortcuts/status row (`? for shortcuts` + `Cooked for`)

Rules:

1. Do not change frozen profile values in-place.
2. If render behavior must change, add a new profile id (e.g. `phase4_locked_v6`) and migrate intentionally.
3. Capture artifacts should record `render_profile` in `meta.json` and `scenario_manifest.json` for traceability.
4. Ground-truth eval artifacts must separate review renders from diagnostics:
   - review captures: `repeat_XX/renders/`
   - diagnostics only: `repeat_XX/diagnostics/*.DIAGNOSTIC_diff_heat_x4.png`
5. Visual review packs must keep heatmaps under lane diagnostics folders only:
   - `lane/diagnostics/DIAGNOSTIC_compare_prev_vs_new_diff_heat_x3.png`
6. Frozen profile constants must match `config/render_profile_freeze_manifest.json` exactly:
   - frozen ids: `phase4_locked_v2`, `phase4_locked_v3`, `phase4_locked_v4`, `phase4_locked_v5`
   - profile order and default id are part of freeze contract.
7. Any frozen-profile drift must fail CI via:
   - workflow: `render-profile-freeze-gate`
   - job: `render profile freeze gate (strict)`

Bless workflow for new profile versions:

1. Run manual workflow:
   - `.github/workflows/render-profile-bless.yml`
   - inputs: `new_profile_id`, `source_profile_id`, `apply_manifest`
2. Or run local script:
   - `python scripts/bless_render_profile_version.py --new-profile-id phase4_locked_v6 --source-profile-id phase4_locked_v5`
3. The bless flow emits a checklist stub in `docs_tmp/cli_phase_5/` and never permits duplicate/malformed profile IDs.
4. After blessing, run:
   - `python scripts/check_render_profile_freeze.py --manifest config/render_profile_freeze_manifest.json`

Height control knobs (frozen interface, user-configurable per capture):

- `--height-mode pane|scrollback`
  - `pane`: keep fixed viewport height from tmux pane (or `--rows` when `--ansi`).
  - `scrollback`: render full captured scrollback into a tall PNG.
- `--rows-override N`
  - force final row count to `N` regardless of pane/scrollback-derived height.
- `--max-rows N`
  - cap final row count at `N` after mode/override resolution.

Recommended patterns:

1. Fixed-height ground-truth parity (default):
   - `python scripts/tmux_capture_to_png.py --target <session:window.pane> --render-profile phase4_locked_v5`
2. Full-history tall render:
   - `python scripts/tmux_capture_to_png.py --target <session:window.pane> --height-mode scrollback --render-profile phase4_locked_v5`
3. Full-history with cap:
   - `python scripts/tmux_capture_to_png.py --target <session:window.pane> --height-mode scrollback --max-rows 120 --render-profile phase4_locked_v5`
4. Explicit fixed preview rows:
   - `python scripts/tmux_capture_to_png.py --target <session:window.pane> --height-mode pane --rows-override 30 --render-profile phase4_locked_v5`
