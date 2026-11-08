# CI consolidation

## Pull requests

`.github/workflows/ci.yml` runs the selector regression test on every push and pull request. On pull requests, the same job selects affected tests after the regression passes, using a blobless checkout with full commit history. If the regression, selector, or selector job fails, safe defaults run the existing PR Python tests and leave the existing TUI and Sidebar test jobs enabled; the selector job itself remains red.

The selector owns changed-path policy and emits `full`, `python`, and `node_targets`. CI consumes those outputs without a second path map. In PR CI, `node_targets` only narrows the existing multi-OS TUI and VSCode Sidebar jobs; it does not add per-package SDK test coverage. The existing `product-spine` SDK test steps are unchanged.

- `full=true` or `python=true` runs the existing PR Python test set (targeted tests plus stream chaos). Python paths always take this conservative path because the repository has no demonstrated Python test ownership map. A full selection also enables the existing TUI and Sidebar jobs.
- Otherwise, CI skips those Python test steps and runs an existing TUI or Sidebar job only when its package appears in `node_targets`. The product-spine tests, including `npm --prefix sdk/ts test` and `npm --prefix sdk/ts run test:consumer`, remain unchanged, as do the other product, build, typecheck, and smoke jobs. Pushes use safe defaults and retain existing tests.
- Pushes always run the existing Python test set.

The whole-repository `python -m pytest -q` sweep is not a PR gate; it runs in the nightly full suite.

The pull-request workflow adds no per-package SDK Node test matrix. The selector only narrows the pre-existing TUI and Sidebar jobs; both still run their existing coverage when selected, and selector failure enables them. `product-spine` keeps its existing dependencies, including its canonical SDK tests.

## Full suite

`.github/workflows/full-suite-nightly.yml` runs at 04:17 UTC on `main` and supports manual dispatch on `main`. A non-main dispatch skips the jobs. It checks the selector's global fallback, runs `python -m pytest -q`, and exercises the established canonical SDK, TUI and Sidebar Node packages. The canonical SDK gets the same Python dependencies as its existing product-spine job. It passes no provider secrets or provider environment variables and does not enable live-provider jobs.

S2 explicitly limited the nightly Node scope to the established coverage after the initial 16-package matrix exposed missing sibling devDependencies and Python server dependencies. Expanding to all SDK packages requires a separately validated dependency-bootstrap plan; it is not shipped as a known setup-red nightly here.

## Guard consolidation

The measured short guard cohort now runs in `ci_guards` on one GitHub-hosted runner: compat-break acknowledgement, zero-importer advisory, danger-zone ACR and contract-pack checks, ATP governance, governance templates, kernel-extension boundaries, request-body hashes, tool-schema hashes, event-envelope snapshots, E4 target-freeze validation, conformance, extension-disabled regression, replay determinism, and evidence-bundle contract tests.

`.github/actions/ci-guards/action.yml` gives each guard its own named steps, continues after each failure, uploads the existing reports, and writes each result to the job summary. The final summary step fails the job when a blocking guard fails. Zero-importer remains advisory and reports its outcome without failing the guard job. The longer conformance-matrix sync and compatibility suite remain separate.

The measured baseline was PR run `38670103799` on 2026-10-15: 31 jobs, 235 seconds wall time, 1,086 job-seconds, and 263 setup seconds. Fourteen active guard/gate jobs in the consolidated cohort each took 6–21 seconds. These are baseline measurements; post-change timings must come from a completed PR run.

## Checkout and dependency caches

CI checkouts use `filter: blob:none`. The selector and consolidated guard job use `fetch-depth: 0` because they compare commits; other jobs keep the shallow default.

`.github/actions/main-seeded-cache/action.yml` restores pip, uv, and npm caches from main and saves them only on pushes to `main`. Pull requests, schedules, and manual runs restore caches without writing them. `setup-python` and `setup-node` implicit caches are disabled; `setup-uv` has its implicit cache disabled. Cache keys include the runner and job, and dependency files where available.

Every job that runs by default uses GitHub-hosted runners. The optional Firecracker job keeps its existing self-hosted runner and remains disabled unless `ATP_FIRECRACKER_SMOKE` is set; this change does not alter runner infrastructure.
