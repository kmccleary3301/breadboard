# Affected-test selection

`scripts/ci/select_tests.py` takes either a base/head commit pair or explicit changed paths. It prints a JSON object and appends these values to `--github-output`:

- `full`: `true` or `false`;
- `python`: `true` when the full Python suite is required;
- `node_targets`: JSON array of repo-relative package paths.

A full selection sets `python=true` and lists every supported Node target. CI treats `full=true` as authoritative. `sdk/ts-workspace` is full-only because it has no lockfile; CI installs that package with `npm install --package-lock=false`. The other targets use their existing `npm test` scripts after `npm ci`.

Only source changes in these package-local trees can narrow Node tests:

- `sdk/ts-orchestration-temporal/src/` → `sdk/ts-orchestration-temporal`;
- `tui_skeleton/src/` → `tui_skeleton`;
- `vscode_sidebar/src/` → `vscode_sidebar`.

All Python changes and all other Node changes select the full suite. This includes tests, `conftest.py`, fixtures, SDK/kernel changes, package/build configuration, lockfiles, CI configuration, and unknown paths. A change to `sdk/ts-kernel-core/src/` therefore selects the full suite, including `sdk/ts-backbone`; its `backbone.test.ts` imports the backbone entry point, whose source depends on kernel-core.

Documentation skips tests only when every changed path is one of the exact allowlisted selector docs: `docs/ci/affected-tests.md` or `docs/ci/consolidation.md`. Other documentation can be test input, so all other docs paths—including the repository README, docs index, provider-plan auth guide, and kernel semantics dossiers—select the full suite. Empty diffs, failed/malformed diffs, and empty explicit `--paths` input also select the full suite. Renames consider both old and new paths, and deletions retain the deleted path for selection.

For deterministic local input:

```sh
python scripts/ci/select_tests.py --paths sdk/ts-orchestration-temporal/src/orchestrator.ts --github-output /tmp/selector-output
```
