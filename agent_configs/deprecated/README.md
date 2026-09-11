# Deprecated agent configurations

Historical profiles stay here with their original bytes and capture-era names.

- `2026-03-06/` contains the four former top-level E4 dossiers and their generated v2 operational variants.
- `2026-03-10/research/` preserves the three ATP research profiles at their evidence-pinned bytes; the runnable copies under `../research/` adjust relative paths for the deeper directory.
- `2026-06-30/` contains the accepted Codex CLI 0.139.0 GPT-5.5 capture-probe config. Its original bytes remain the evidence-pinned artifact; the current top-level Codex profile fixes the untracked prompt dependency and states the same narrow claim boundary.

Do not use these paths as current defaults. `manifest.json` records each original logical path, its archive location, and the SHA-256 of its original bytes.

E4 evidence readers resolve a missing logical config through this manifest and verify the archive hash on every lookup. Unknown entries do not trigger filesystem searches; missing or modified archives and paths escaping this directory fail closed. Existing files take precedence and remain subject to their evidence hash checks.

This is evidence-location metadata, not a public agent-config alias. It leaves captures, ledger inputs, frozen declarations, and their pins unchanged. Current commands should select a top-level profile or a runnable research profile.
