# KyleCode Phase 4 Runbook

## 1. Quick Reference

- Latest routing + telemetry instrumentation: P1/P2 deliverables (P1.1–P1.5, P2.1–P2.3)
- Automation: `python scripts/run_regression_matrix.py`
- Vendor bundle: `python scripts/build_vendor_bundle.py logging/<run>`
- Metrics export: `python scripts/export_provider_metrics.py logging/<run>/meta/telemetry.jsonl --print-summary`

## 2. Before a Run

1. Set environment variables:
   ```bash
   export AGENT_SCHEMA_V2_ENABLED=1
   export KC_DISABLE_PROVIDER_PROBES=0
   export RAY_SCE_LOCAL_MODE=1           # optional for local testing
   export OPENROUTER_API_KEY=<key>
   export RAYCODE_TELEMETRY_PATH=/var/log/kylecode/provider_metrics.jsonl  # optional central sink
   ```
2. Confirm configs include routing overrides (`providers.models[].routing`).
3. Optional: enable response normaliser (`features.response_normalizer: true`).

## 3. During / After a Run

| Artifact | Location | Notes |
| --- | --- | --- |
| Capability probes | `meta/capability_probes.json` | Probe outcome per route. |
| Provider metrics | `meta/provider_metrics.json` | Per-route latency, error, overrides, fallback counters. |
| Telemetry JSONL | `meta/telemetry.jsonl` or `$RAYCODE_TELEMETRY_PATH` | Append-only stream of telemetry events (use exporter script). |
| Structured requests | `meta/requests/*.json` | Sanitised header + body excerpts. |
| Conversation log | `conversation/conversation.md` | Look for `[stream-policy]`, `[fallback_route]`, `[circuit-open]`. |
| Raw responses | `raw/responses/` | Includes base64/HTML excerpts. |
| Error payloads | `errors/turn_*.json` | Matches metadata stored in provider_metrics. |

## 4. Regression Matrix (P2.1)

Run the automated suite nightly or before releases:
```bash
python scripts/run_regression_matrix.py
```
This executes `tests/test_provider_regression_matrix.py`, covering openrouter streaming fallback, openrouter text-only, and fallback-to-openai scenarios. Failures emit detailed stream/fallback diagnostics.

## 5. Telemetry Integration (P2.2)

* Default: `meta/telemetry.jsonl` per run.
* Custom sink: set `RAYCODE_TELEMETRY_PATH` to a shared JSONL path.
* Export/aggregate:
  ```bash
  python scripts/export_provider_metrics.py logging/<run>/meta/telemetry.jsonl --print-summary
  python scripts/export_provider_metrics.py /var/log/kylecode/provider_metrics.jsonl --http-endpoint https://monitoring.example.com/ingest
  ```

## 6. Vendor Escalation (P2.3)

1. Build bundle:
   ```bash
   python scripts/build_vendor_bundle.py logging/<run>
   ```
   Output: `<run>_vendor_bundle.zip` containing capability probes, structured requests, provider metrics, raw diagnostics, and conversation transcript.
2. Attach bundle + regression matrix output to the support ticket.
3. Include summary (call counts, HTML errors, fallback routes) using `meta/provider_metrics.json` or telemetry exporter output.

## 7. Incident Checklist (P2.4)

1. Gather latest run directory (`logging/<timestamp>_agent_ws_*`).
2. Export metrics summary:
   ```bash
   python scripts/export_provider_metrics.py logging/<run>/meta/telemetry.jsonl --print-summary > incident_metrics.json
   ```
3. Capture structured requests and error payloads (already included in vendor bundle).
4. Attach vendor bundle ZIP to incident ticket.
5. Run regression matrix to confirm whether issue reproduces under harness.

## 8. Optional Investigation (P2.5)

* For direct vendor API validation, use `agent_configs/opencode_openai_gpt4o_mini_c_fs_perturn_append.yaml` or `agent_configs/opencode_openai_gpt5nano_c_fs_direct.yaml` as fallbacks.
* Update routing preferences to prioritise direct OpenAI (`providers.models[].routing.fallback_models`).
* Compare metrics between OpenRouter and direct runs using telemetry exporter to quantify improvements.

