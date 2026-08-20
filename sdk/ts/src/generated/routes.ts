// GENERATED FILE - do not edit by hand.
// generator: scripts/dev/generate_ts_sdk.py (deterministic, in-process, no network)
// openapi-schema-sha256: 432b37826623941a5b10c332468cf4f52a5af81c205721333851b3de9446689c
// app-source-sha256: 93b5c80ff32b2403f480de2ee846fbe2c4328c2059774bd0e20f76755627d002

export interface RouteEntry { path: string; method: string; operationId: string }

export const ROUTES: readonly RouteEntry[] = [
  { path: "/v1/artifacts", method: "GET", operationId: "artifact.list" },
  { path: "/v1/artifacts/{artifact_id}", method: "GET", operationId: "artifact.get" },
  { path: "/v1/artifacts/{artifact_id}/verify", method: "POST", operationId: "artifact.verify" },
  { path: "/v1/harness-locks/{lock_id}", method: "GET", operationId: "harness_lock.get" },
  { path: "/v1/harnesses", method: "GET", operationId: "harness.list" },
  { path: "/v1/harnesses", method: "POST", operationId: "harness.create" },
  { path: "/v1/harnesses/{harness_id}", method: "GET", operationId: "harness.get" },
  { path: "/v1/harnesses/{harness_id}", method: "PUT", operationId: "harness.update" },
  { path: "/v1/harnesses/{harness_id}/explain", method: "POST", operationId: "harness.explain" },
  { path: "/v1/harnesses/{harness_id}/lock", method: "POST", operationId: "harness.lock" },
  { path: "/v1/harnesses/{harness_id}/validate", method: "POST", operationId: "harness.validate" },
  { path: "/v1/health", method: "GET", operationId: "system.health" },
  { path: "/v1/integrations", method: "GET", operationId: "integration.list" },
  { path: "/v1/integrations/{integration_id}", method: "GET", operationId: "integration.get" },
  { path: "/v1/integrations/{integration_id}/probe", method: "POST", operationId: "integration.probe" },
  { path: "/v1/schemas", method: "GET", operationId: "system.schemas" },
  { path: "/v1/sessions", method: "GET", operationId: "session.list" },
  { path: "/v1/sessions", method: "POST", operationId: "session.start" },
  { path: "/v1/sessions/{session_id}", method: "GET", operationId: "session.get" },
  { path: "/v1/sessions/{session_id}/approve", method: "POST", operationId: "session.approve" },
  { path: "/v1/sessions/{session_id}/artifacts", method: "GET", operationId: "session.artifacts" },
  { path: "/v1/sessions/{session_id}/cancel", method: "POST", operationId: "session.cancel" },
  { path: "/v1/sessions/{session_id}/events", method: "GET", operationId: "session.events" },
  { path: "/v1/sessions/{session_id}/input", method: "POST", operationId: "session.send_input" },
  { path: "/v1/sessions/{session_id}/resume", method: "POST", operationId: "session.resume" },
  { path: "/v1/system", method: "GET", operationId: "system.describe" },
] as const;
