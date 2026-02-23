# Webapp E2E Scenarios

Deterministic Playwright scenarios for baseline webapp QC and regression checks.

## Scenario Pack (V1)

- `webapp.spec.ts`:
  - shell rendering + diagnostics health path
  - replay import hydration path
  - connection mode/token policy persistence
  - create/attach/send/permissions/checkpoint/files/artifact live flow
  - sequence-gap recovery flow

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

## Artifacts

- report root: `artifacts/webapp_e2e/`
- test output: `artifacts/webapp_e2e/test-results/`
- html report: `artifacts/webapp_e2e/html-report/`
- report json: `artifacts/webapp_e2e/report.json`
- summary json: `artifacts/webapp_e2e/summary.json`
- curated scenario screenshots are attached on pass/fail for key checkpoints
