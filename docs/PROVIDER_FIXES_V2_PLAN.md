# Provider Fixes v2 Plan

## Objectives
- Restore reliable tool-call execution for OpenRouter-backed models (GLM-4.6, Grok-4-fast, DeepSeek v3.2-exp) by eliminating non-JSON/HTML payload failures and post-turn stalls.
- Encode GPT-5 Pro recommendations (report dated 2025-10-10) into actionable engineering work with clear priorities, owners, and validation checkpoints.
- Strengthen KyleCode’s provider abstraction so future multi-provider regressions are detected, mitigated, and escalated automatically.

## Scope & Non-Goals
- **In scope:** Client-side changes (headers, streaming handling, retries), capability probing, health scoring, logging/monitoring upgrades, regression matrix, vendor escalation package.
- **Out of scope (for this plan):** Replacing OpenRouter entirely, major refactors of the agentic loop, or commitments to specific alternative model vendors.

## Milestones & Workstreams

### Milestone P0 — Guardrails & Immediate Mitigations (Target: 3 working days)
| Task | Description | Owner | Notes |
| --- | --- | --- | --- |
| P0.1 | Enforce `Accept: application/json; charset=utf-8` and `Accept-Encoding: identity` on all OpenRouter calls; explicitly branch on `Content-Type`. | TBD (Runtime) | Update `ProviderRuntime.invoke/_call_with_raw_response`; gate `.json()` on JSON types; record route metadata. |
| P0.2 | Disable streaming (`stream=false`) for tool turns on OpenRouter routes until SSE handling is validated; fall back to non-JSON `response_format` when JSON mode fails. | TBD (Agent loop) | Configurable per provider; record decision in session metadata. |
| P0.3 | Add SSE parser path for `text/event-stream` responses (no `.json()`); short-circuit to non-stream if SSE parsing fails once. | TBD (Runtime) | Use incremental parser; unify with new response normalizer stub. |
| P0.4 | Expand logging: capture response headers (incl. OpenRouter request IDs), raw bytes (base64), and decode results (`raw_excerpt`, `html_excerpt`). | TBD (Logging) | Extend LoggerV2 to persist artifacts; ensure secrets scrubbed. |
| P0.5 | Implement automatic retry & fallback: one retry on same route, then reroute to closest alternative; mark route “degraded”. | TBD (Routing) | Simple exponential backoff with jitter; leverage existing provider metadata bucket. |

### Milestone P1 — Routing Hardening & Health Scoring (Target: +7 working days)
| Task | Description | Owner | Notes |
| --- | --- | --- | --- |
| P1.1 | Capability probe on startup per route (stream vs non-stream × tool call × JSON mode). Persist profile for runtime decisions. | TBD | Use lightweight no-op tool to validate; store results in provider metadata cache. |
| P1.2 | Implement provider health scoring & circuit breakers (rolling success/error/stall rates). | TBD | Define thresholds (e.g., >3 HTML/non-JSON errors/10 min => open circuit 15 min). |
| P1.3 | Response normalizer v0.1 that emits uniform internal events for JSON & SSE. | TBD | Wrap around `ProviderResult`; ensure transcript identical to legacy flow. |
| P1.4 | Structured request recorder w/ sanitized headers + 2 KB body excerpt for incidents. | TBD (Telemetry) | Reuse LoggerV2 root; align with incident response template. |
| P1.5 | Update agent configs to reference health map (e.g., prefer “good” routes, disable plan mode automatically when plan_turn_limit reached). | ✅ | Routing config now drives probe-aware streaming/tool policies and prioritized fallbacks; IR/Markdown capture each decision. |

### Milestone P2 — Regression Matrix, Monitoring, Vendor Engagement (Target: +14 working days)
| Task | Description | Owner | Notes |
| --- | --- | --- | --- |
| P2.1 | Automated regression suite covering providers × streaming × tool combos. | ✅ | `tests/test_provider_regression_matrix.py` + `scripts/run_regression_matrix.py` run the harness. |
| P2.2 | Monitoring/alerting: HTML detection rate, stall duration, per-route latency, circuit breaker state. | ✅ | Metrics written to `meta/provider_metrics.json` + telemetry JSONL; `scripts/export_provider_metrics.py` forwards data to monitoring. |
| P2.3 | Vendor escalation kit (OpenRouter + upstreams): reproducible scripts, captured headers/body, IR snippets, checklist. | ✅ | `scripts/build_vendor_bundle.py` bundles structured requests, metrics, logs. |
| P2.4 | Update runbooks & incident templates; document change-log entry for stakeholders. | ✅ | `docs/phase_4/runbook.md` covers incident workflow, metric export, regression automation. |
| P2.5 | Evaluate direct vendor APIs as fallback (optional investigation). | ✅ | Configs prefer direct OpenAI fallbacks; use telemetry exporter to compare provider performance. |

## Validation & Testing Strategy
1. **Unit/E2E updates:** Add coverage for new header logic, SSE parser, logging artifacts, circuit breaker transitions.
2. **Regression matrix (P2.1):** Run nightly and pre-deploy; require zero HTML/timeout events before shipping.
3. **Smoke tests:** Repeat C proto filesystem task for each provider post-fix; verify successful tool execution and clean IR finish (no `ProviderRuntimeError`).
4. **Observability checks:** Confirm monitoring dashboards show expected metrics during canary rollouts.

## Monitoring & Alerting Enhancements
- Metrics: `provider_html_payload_rate`, `provider_raw_excerpt_missing`, `provider_stall_count`, `route_health_state`.
- Alerts: HTML payloads >2/min per route; stall >30 s; circuit breaker open >15 min. Integrate with Slack/PagerDuty.

## Escalation Plan
- Compile vendor packages once P0 guardrails are live and failures persist: include request IDs, headers, body excerpts, IR snippets.
- Contact OpenRouter support with structured checklist; parallel outreach to Zhipu/xAI/DeepSeek per GPT-5 §4.
- If unresolved within 48 h, implement temporary routing to known-stable providers (OpenAI native) or disable affected models.

## Dependencies & Risks
- Availability of owners across runtime, routing, logging, and ops teams.
- Potential need for additional SSE parsing libraries or HTTP client tweaks (httpx settings, HTTP/1.1 fallback).
- Upstream providers may throttle testing; coordinate with vendor support to avoid rate limits.

## Open Questions
1. Do we have budget to maintain direct vendor accounts for failover if OpenRouter issues persist? (Decision pending leadership.)
2. Should we introduce a centralized provider proxy to normalize responses before they reach the agent loop? (Deferred until normalizer v0.1 results.)
3. What storage/quarantine requirements apply to raw response payloads (compliance/PII review)?

## Next Steps
1. Assign owners for P0 tasks; schedule kick-off meeting within 24 h.
2. Draft detailed technical tickets referencing this plan and GPT-5 Pro report sections.
3. After P0 deployment and validation, revisit plan for P1/P2 sequencing and adjust timelines.
