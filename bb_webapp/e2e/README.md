# Webapp E2E Scenarios

Deterministic Playwright scenarios for baseline webapp QC and regression checks.

## Scenario Pack (V1)

- `webapp.spec.ts`:
  - shell rendering + diagnostics health path
  - replay import hydration path
  - connection mode/token policy persistence

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

## Artifacts

- test output: `e2e/artifacts/test-results/`
- html report: `e2e/artifacts/html-report/`
