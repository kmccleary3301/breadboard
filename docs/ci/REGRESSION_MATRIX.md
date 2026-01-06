# Provider Regression Matrix CI Guide

KyleCode now ships a regression harness that exercises the key provider × streaming × tool combinations we care about for resiliency (see Phase 4 P2.1). To run it in automation:

1. Ensure the following environment variables are set so the conductor operates in mock/local mode:
   * `AGENT_SCHEMA_V2_ENABLED=1`
   * `KC_DISABLE_PROVIDER_PROBES=0`
   * `RAY_SCE_LOCAL_MODE=1`
   * `OPENROUTER_API_KEY=<any non-empty value>` (mock runtime still expects the env var)
2. Invoke `python scripts/run_regression_matrix.py`. The helper sets the environment defaults listed above and runs `pytest tests/test_provider_regression_matrix.py -q`.
3. Add this step to the nightly/CI workflow so we detect regressions in routing overrides, fallback handling, and capability-probe integrations before they hit production.

Example GitHub Actions snippet:

```yaml
- name: Provider regression matrix
  run: |
    python scripts/run_regression_matrix.py
```

Artifacts to monitor:

* Each regression scenario emits `provider_metrics` telemetry events (see `meta/provider_metrics.json` and `meta/telemetry.jsonl` in the logging directory).
* Failures surface detailed routing diagnostics (stream-policy/fallback IR events) to help triage.
