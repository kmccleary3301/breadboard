# CI consolidation

## Pull requests

`.github/workflows/ci.yml` runs the selector regression test on every push and pull request. On pull requests, the same job runs the selector only after that regression passes, using a blobless checkout with full commit history. If the regression or selector fails, CI falls back to the full PR test set and all Node targets, then reports the selector job as failed.

The selector owns changed-path policy and emits `full`, `python`, and `node_targets`. CI consumes those outputs without a second path map:

- `full=true` or `python=true` runs the existing PR Python test set (targeted tests plus stream chaos) and, for `full`, all 16 Node targets. Python paths always take this conservative path because the repository has no demonstrated Python test ownership map.
- Otherwise, CI skips those Python test steps and runs only the selected Node targets. The existing product, cross-platform, typecheck, build, and smoke jobs remain in place.
- Pushes always run the existing Python test set.

The whole-repository `python -m pytest -q` sweep is not a PR gate; it runs in the nightly full suite.

The Node matrix runs each package's existing `npm test` script. Locked packages use `npm ci`; `sdk/ts-workspace` has no lockfile, so CI uses `npm install --package-lock=false`. The TUI target builds its local kernel-contracts dependency before installing and testing the TUI package. The required `product-spine` status keeps its name and its existing dependencies (product spine checks and product compilation). New selector, guard, and Node jobs report separately; adding them to the required status is a branch-protection decision outside this change.

## Full suite

`.github/workflows/full-suite-nightly.yml` runs at 04:17 UTC on `main` and supports manual dispatch on `main`. A non-main dispatch skips the jobs. The workflow asks the selector for the global CI-config path, which selects full Python and every Node target. It runs the selector regression before consuming that result, then runs `python -m pytest -q` and every Node package test. It passes no provider secrets or provider environment variables and does not run live-provider jobs.

## Guard consolidation

The measured short guard cohort now runs in `ci_guards` on one GitHub-hosted runner: compat-break acknowledgement, zero-importer advisory, danger-zone ACR and contract-pack checks, ATP governance, governance templates, kernel-extension boundaries, request-body hashes, tool-schema hashes, event-envelope snapshots, E4 target-freeze validation, conformance, extension-disabled regression, replay determinism, and evidence-bundle contract tests.

`.github/actions/ci-guards/action.yml` gives each guard its own named steps, continues after each failure, uploads the existing reports, and writes each result to the job summary. The final summary step fails the job when a blocking guard fails. Zero-importer remains advisory and reports its outcome without failing the guard job. The longer conformance-matrix sync and compatibility suite remain separate.

The measured baseline was PR run `38670103799` on 2026-10-15: 31 jobs, 235 seconds wall time, 1,086 job-seconds, and 263 setup seconds. Fourteen active guard/gate jobs in the consolidated cohort each took 6–21 seconds. These are baseline measurements; post-change timings must come from a completed PR run.

## Checkout and dependency caches

CI checkouts use `filter: blob:none`. The selector and consolidated guard job use `fetch-depth: 0` because they compare commits; other jobs keep the shallow default.

`.github/actions/main-seeded-cache/action.yml` restores pip, uv, and npm caches from main and saves them only on pushes to `main`. Pull requests, schedules, and manual runs restore caches without writing them. `setup-python` and `setup-node` implicit caches are disabled; `setup-uv` has its implicit cache disabled. Cache keys include the runner and job, and dependency files where available.

Every job that runs by default uses GitHub-hosted runners. The optional Firecracker job keeps its existing self-hosted runner and remains disabled unless `ATP_FIRECRACKER_SMOKE` is set; this change does not alter runner infrastructure.
