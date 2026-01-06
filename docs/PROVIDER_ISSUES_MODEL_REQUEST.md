# Request: Diagnosis And Remediation Plan For Persistent OpenRouter Provider Failures

Hello GPT-5-Pro,

We are running an agentic coding platform (KyleCode) that orchestrates multiple LLM providers. Our OpenRouter-backed models are repeatedly failing or stalling, yielding HTML payloads or truncated responses instead of valid JSON. Despite swapping API keys, tightening the run-loop, and adding diagnostics, the issues persist across several providers (Zhipu GLM-4.6, xAI Grok-4-fast, DeepSeek v3.2-exp). We need a comprehensive, no-assumptions analysis that explains the likely root causes, enumerates corrective actions (both client-side and with OpenRouter/vendor ops), and proposes monitoring/alerting improvements. Please treat this as a mini-incident response engagement and produce an exhaustive report (~20+ pages) covering the items below.

## Attachments
- @agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml
- @agent_configs/opencode_openrouter_deepseek_v32exp_c_fs_perturn_append.yaml
- @agent_configs/opencode_grok4fast_c_fs_v2.yaml
- @logging/20251010-042623_agent_ws_opencode/meta/conversation_ir.json
- @logging/20251010-042623_agent_ws_opencode/errors/turn_6.json
- @logging/20251010-042623_agent_ws_opencode/conversation/conversation.md
- @logging/20251010-041037_agent_ws_opencode/meta/conversation_ir.json
- @logging/20251010-041037_agent_ws_opencode/conversation/conversation.md
- @logging/20251010-035556_agent_ws_opencode/conversation/conversation.md
- @logging/20251010-041110_agent_ws_opencode/conversation/conversation.md

Please review each attachment; they show the current configuration inheritance, run transcripts, IR snapshots, and the most recent provider error artifact (now including a `raw_excerpt` field with the full HTTP body).

## Context & Symptoms
1. **GLM-4.6 via OpenRouter (Zhipu)**
   - Consistently returns HTML/whitespace payloads on tool invocation attempts. The latest capture (`turn_6.json`) shows `html_detected=false` but `raw_excerpt` filled with whitespace, suggesting compressed or stripped content arriving despite a 200 status.
   - With a new API key, the first two plan turns succeed, but subsequent turns fail immediately when the agent transitions to build mode.

2. **Grok-4-fast via OpenRouter**
   - Earlier runs produced HTML errors; after enforcing `plan_turn_limit=1` and allowing multiple tools per turn, the agent now reaches build mode but tool executions still fail validation and eventually stop without progress.

3. **DeepSeek v3.2-exp via OpenRouter**
   - The model responds to the first user prompt, then the connection stalls indefinitely with no further tokens and no provider error. We forcibly terminate after a timeout; no raw response is captured beyond the first turn.

4. **General**
   - All failures happen despite healthy usage on OpenAI-native configs in the same environment.
   - We’ve added runtime instrumentation to capture `raw_excerpt` and `html_excerpt` fields, plus structured IR logs for every turn.
   - We suspect OpenRouter is returning HTML maintenance/rate-limit pages (possibly compressed) or there are upstream TLS/proxy issues before the JSON payload is delivered.

## Deliverables Requested (Deep Dive)
Please respond with a structured, deeply reasoned report covering the following sections. For each section, reference the attachments and supply actionable recommendations, timelines, and confidence levels.

### 1. Failure Mode Taxonomy
1.1 Enumerate plausible causes for non-JSON bodies despite HTTP 200 (e.g., upstream load balancers, gzip corruption, Cloudflare interstitials, mis-specified `Accept` headers). Rank by likelihood given the evidence.
1.2 Explain why `html_detected` stayed false while `raw_excerpt` is whitespace—what encodings or truncation strategies could produce this signature?
1.3 For DeepSeek, outline scenarios where a stream could hang after the first turn with no error (server-side keep-alive, streaming chunk parsing fail, request body mismatch, etc.). Suggest packet-level traces or logging to validate each hypothesis.

### 2. Attachment Analysis
2.1 Inspect the IR and conversation logs to reconstruct the exact sequence of prompts and responses. Identify where each provider’s behavior diverges from expectations.
2.2 Interpret the captured `raw_excerpt` and explain whether it is likely compressed HTML, a null body, or intentional whitespace. Recommend how to decode or store the payload to preserve original bytes.
2.3 From the configs, analyze inherited settings (e.g., tool prompt synthesis, plan mode constraints, retry/backoff) that might interact poorly with these providers.

### 3. Client-Side Mitigations
3.1 Recommend short-term guardrails (e.g., stricter response validation, fallback provider routing, automatic retry with alternate endpoints, forced gzip disable) and estimate impact on task success.
3.2 Propose longer-term architectural changes: request signing, more granular capability probing, or per-provider health scoring. Include pseudo-code or flowcharts where useful.
3.3 Suggest monitoring metrics and dashboards we should add (HTTP status mix, HTML detection rate, per-provider latencies) and outline alert thresholds.

### 4. Provider / Vendor Engagement Plan
4.1 Draft the questions and artifacts we should send to OpenRouter support and to each upstream model vendor (Zhipu, xAI, DeepSeek) to accelerate diagnosis.
4.2 Identify the minimal reproducible tests we can hand them (including expected request/response payloads) and any sensitive data to redact.
4.3 Recommend escalation paths if the issue persists (e.g., bypass OpenRouter, use direct vendor APIs, set up temporary proxy infrastructure).

### 5. Validation & Regression Testing
5.1 Design a test matrix that exercises multiple providers, schemas (chat vs responses), and tool call scenarios to verify any fix.
5.2 Specify how to automate detection of HTML payloads or silent stalls in CI, including thresholds, log parsing, and alert routing.

### 6. Documentation & Knowledge Transfer
6.1 Provide a concise change-log entry summarizing detection, mitigation, and next steps for internal stakeholders.
6.2 Recommend updates to our runbooks and incident response templates so future engineers can diagnose similar issues faster.

### 7. Open Questions / Additional Research
7.1 Highlight any gaps in the supplied data that limit certainty, and prescribe what further telemetry (HTTP headers, TLS fingerprints, trace IDs) we should collect.
7.2 Suggest any openly available resources—RFCs, vendor docs, prior incidents—that we should review to deepen our understanding of OpenRouter’s infrastructure.

Please weave in diagrams or tables where appropriate, call out confidence levels for key assertions, and include a prioritized action list (P0/P1/etc.) at the end. Thank you.
