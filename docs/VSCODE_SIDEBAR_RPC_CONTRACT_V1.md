# VSCode Sidebar Host-Webview RPC Contract (V1)

Last updated: `2026-02-21`

This document defines the `v1` RPC envelope and required methods for BreadBoard's VSCode/Cursor sidebar client.

## Goals

1. Keep host<->webview protocol explicit and versioned.
2. Keep engine protocol usage centralized in extension host.
3. Preserve deterministic event ordering and replay semantics.
4. Harden message handling with runtime validation.

## Transport Topology

1. Extension host talks to BreadBoard engine over HTTP + SSE.
2. Webview talks only to extension host over `postMessage`.
3. Webview never holds engine tokens.

## Envelope

All host<->webview messages must match one of these shapes.

```ts
type RpcReq = {
  v: 1;
  kind: "req";
  id: string;
  method: string;
  params?: unknown;
};

type RpcRes = {
  v: 1;
  kind: "res";
  id: string;
  ok: boolean;
  result?: unknown;
  error?: { code: string; message: string; details?: unknown };
};

type RpcEvt = {
  v: 1;
  kind: "evt";
  topic: "bb/connection" | "bb/state" | "bb/events";
  payload: unknown;
};
```

Invalid messages must be rejected and logged as protocol violations.

## Required Request Methods

## Session lifecycle

- `bb.init()`
- `bb.createSession({ configPath?: string; task?: string; model?: string; permissionMode?: string })`
- `bb.listSessions()`
- `bb.selectSession({ sessionId: string })`
- `bb.deleteSession({ sessionId: string })`

## Run controls

- `bb.sendMessage({ sessionId: string; text: string; attachmentIds?: string[] })`
- `bb.stop({ sessionId: string })`
- `bb.command({ sessionId: string; command: string; args?: Record<string, unknown> })`

## Permissions

- `bb.approvePermission({ sessionId: string; requestId: string; decision: "allow_once"|"deny"|"allow_rule"; ruleUpdate?: Record<string, unknown> })`

## Files and artifacts

- `bb.listFiles({ sessionId: string; path: string })`
- `bb.readFileSnippet({ sessionId: string; path: string; maxBytes?: number; headLines?: number; tailLines?: number })`
- `bb.openDiff({ sessionId: string; filePath: string; beforeRef?: string; afterRef?: string })`
- `bb.downloadArtifact({ sessionId: string; artifactPath: string })`

## Event Topics

## `bb/connection`

```ts
{
  status: "connecting" | "connected" | "error";
  sessionId?: string;
  retryCount?: number;
  lastError?: string;
  gapDetected?: boolean;
}
```

## `bb/state`

```ts
{
  sessions: Array<{
    sessionId: string;
    title?: string;
    status?: string;
    updatedAt?: number;
  }>;
  activeSessionId?: string;
  running?: boolean;
  pendingPermissionCount?: number;
}
```

## `bb/events`

Payload is a batch from host reducer queue:

```ts
{
  sessionId: string;
  events: Array<{
    id: string;        // engine resume token (opaque)
    type: string;
    turn?: number;
    ts?: number;
    payload: Record<string, unknown>;
  }>;
}
```

Rules:

1. Preserve order exactly as received from engine SSE.
2. Do not mutate event ids.
3. Unknown event types must be forwarded/rendered safely.

## Resume and Replay

Host behavior:

1. Persist last seen event id per session.
2. Reconnect with `Last-Event-ID`.
3. If replay gap is detected, emit connection update with `gapDetected=true`.
4. Trigger rebuild workflow from run artifacts when supported.

Webview behavior:

1. Show reconnect state.
2. Show explicit continuity warning on gap detection.

## Security Requirements

1. Validate every inbound and outbound RPC payload with runtime schemas.
2. Ignore unknown method names.
3. Never pass secrets/tokens into webview payloads.
4. Require workspace trust for write/exec actions.
5. Use strict webview CSP and restricted resource roots.

## Performance Requirements

1. Host batches event updates before posting to webview.
2. Webview applies batched updates; avoid per-token re-render loops.
3. Large payloads are truncated in transcript preview surfaces.

## Versioning Rules

1. Additive fields are allowed in `v1`.
2. Breaking shape changes require `v` bump.
3. Any version bump must be reflected in:
   - this doc,
   - extension compatibility checks,
   - contract tests.

## Verification Checklist

- [ ] Runtime schema tests for all request/response envelopes.
- [ ] Unknown method rejection tests.
- [ ] Resume with Last-Event-ID integration test.
- [ ] Gap detection and UI event emission test.
- [ ] Token isolation test (webview cannot access secret material).

