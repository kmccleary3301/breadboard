# Webapp E2E Scenarios

Deterministic Playwright scenarios for baseline webapp QC and regression checks.

## Scenario Pack (V1)

- `webapp.spec.ts`:
  - shell rendering + diagnostics health path
  - replay import hydration path
  - connection mode/token policy persistence
  - create/attach/send/permissions/checkpoint/files/artifact live flow
  - sequence-gap recovery flow
  - invalid remote endpoint failure path
  - remote auth failure and recovery path
- Project matrix:
  - `desktop-chromium`
  - `mobile-chromium`

## Run

```bash
cd bb_webapp
npm run e2e:spec
```

Debug mode (headed + full trace/video):

```bash
cd bb_webapp
npm run e2e:debug
```

CI-style run with summary artifact:

```bash
cd bb_webapp
npm run e2e:ci
```

Artifact quality-gate only:

```bash
cd bb_webapp
npm run e2e:validate
```

## Artifacts

- report root: `artifacts/webapp_e2e/`
- test output: `artifacts/webapp_e2e/test-results/`
- html report: `artifacts/webapp_e2e/html-report/`
- report json: `artifacts/webapp_e2e/report.json`
- summary json: `artifacts/webapp_e2e/summary.json`
- quality gate json: `artifacts/webapp_e2e/quality_gate.json`
- curated scenario screenshots are attached on pass/fail for key checkpoints
- focused panel captures are also attached (transcript/permissions/tools/task tree) for faster visual QC
- deterministic panel snapshots are asserted in-test with Playwright snapshot baselines

See `bb_webapp/e2e/QC_MATRIX.md` for the full matrix and required visual targets.
