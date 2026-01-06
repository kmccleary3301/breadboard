# Provider Metrics Telemetry

Every agent run now persists a structured view of provider health events so Ops can chart HTML/error rates and fallback usage.

## Files per run

| Path | Description |
| --- | --- |
| `meta/provider_metrics.json` | Snapshot of call counts, latencies, errors, stream/tool overrides, and fallback transitions per route. |
| `meta/telemetry.jsonl` | JSONL stream (one event per line) produced by `TelemetryLogger`; each run adds a `provider_metrics` event suitable for ingestion into downstream monitoring systems. |

## Collector overview

The conductor instantiates `ProviderMetricsCollector` for every run and records:

* per-call latency (avg/max) and success/error counts
* stream/tool override occurrences (including capability-probe based decisions)
* fallback transitions and circuit-skip events

On completion, the metrics snapshot is:

1. Stored in `SessionState.provider_metadata["provider_metrics"]`
2. Written to `meta/provider_metrics.json`
3. Emitted via `TelemetryLogger` (file defaults to `meta/telemetry.jsonl`, overridable with `RAYCODE_TELEMETRY_PATH`)

## Shipping to Ops

* **File-based ingestion**: leave `RAYCODE_TELEMETRY_PATH` unset to automatically append telemetry events to `<run>/meta/telemetry.jsonl`, then synchronize that file to the monitoring pipeline.
* **External sink**: set `RAYCODE_TELEMETRY_PATH=/var/log/kylecode/provider_metrics.jsonl` (or equivalent) before launching the agent to stream metrics to a shared location.
* **Batch export**: use `python scripts/export_provider_metrics.py logging/<run>/meta/telemetry.jsonl --print-summary` to produce an aggregated JSON summary, or add `--http-endpoint https://monitoring.example.com/ingest` to forward every `provider_metrics` event.

Each telemetry event has the following schema:

```json
{
  "event": "provider_metrics",
  "summary": { "calls": 2, "errors": 1, ... },
  "routes": {
    "openrouter/x-ai/grok-4-fast": {
      "calls": 2,
      "latency_avg": 0.42,
      "html_errors": 1,
      ...
    }
  },
  "fallbacks": [
    { "from": "openrouter/x-ai/grok-4-fast", "to": "mock/dev", "reason": "mock primary failure" }
  ],
  "stream_overrides": [
    { "route": "openrouter/x-ai/grok-4-fast", "reason": "capability_probe_stream_failure" }
  ],
  "tool_overrides": []
}
```

Use these fields to drive dashboards/alerts (e.g., HTML error rate > 2/min, repeated fallbacks on the same route).
