# BreadBoard Webapp Runbook

## 1. Mode Selection

- `local`: loopback engine development.
- `sandbox`: isolated development runtime.
- `remote`: external/shared engine endpoint.

Always validate mode before operating on live sessions.

## 2. Remote-Safe Setup

1. Select `remote` mode.
2. Enter explicit remote base URL (never rely on inherited local URL).
3. Set token policy:
   - `session-only` for short-lived/operator sessions.
   - `persisted` only when explicitly required.
4. Paste bearer token and run `Diagnostics`.
5. Confirm diagnostics show healthy `health/status/models` results.

## 3. Auth and Failure Troubleshooting

- `HTTP 401/403` messages indicate auth posture mismatch.
- Verify:
  - mode is `remote`,
  - token is present and current,
  - base URL targets expected environment.
- Re-run diagnostics after any token/base URL update.

## 4. Stream Recovery

- If runtime pill enters `gap`:
  1. Click `Recover Stream`.
  2. If still gapped, re-attach session from Sessions list.
  3. Use replay import/export for forensic comparison if needed.

## 5. Checkpoint Restore Flow

1. Refresh checkpoints.
2. Select checkpoint id.
3. Restore and confirm prompt.
4. Wait for auto-reattach and resumed stream state.
5. Validate transcript/tools/task tree consistency.

## 6. Permission Governance

- Resolve pending permission requests with explicit scope/rule.
- Inspect ledger by tool/scope/decision filters.
- Revoke action uses `permission_decision` (`decision: revoke`) and only falls back to legacy revoke command compatibility.
- If revoke still fails, treat it as bridge limitation and record the error in incident capture.

## 7. CSRF/CORS Posture (Operator Guidance)

- Prefer loopback/local network restrictions for local mode.
- Remote deployments should terminate TLS and enforce origin policy at gateway.
- Do not expose broad CORS wildcard policies for credentialed routes.
- Keep bearer tokens out of URL/query parameters; use Authorization headers only.

## 8. Incident Capture

When reporting incident/regression, capture:

- projection hash,
- last 120 raw events (redacted view),
- diagnostics output,
- replay export package,
- runtime state transitions from audit log,
- CI run URL + job id for `webapp_gate` and `Python (ubuntu)` checks.
