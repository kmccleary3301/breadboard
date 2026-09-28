# ACR-20260924-omp-native-worker-no-preconnect

- `acr_id`: `ACR-20260924-omp-native-worker-no-preconnect`
- `title`: Oh My Pi 18.1.17 native worker opens no provider connection
- `author`: BreadBoard E4 Oh My Pi lane
- `date`: 2026-09-24
- `status`: implemented; DO-2 installed replay and independent review pending

## 1) Problem Statement

DO-2 job 1257 stalled on BreadBoard's first OMP provider request and timed out after 45 s. The native worker passes the lease-bound `model` to pinned `createAgentSession`. Pinned `sdk.ts:1642` then calls `preconnectModelHost(model.baseUrl)`, and `sdk.ts:4351-4360` calls Bun's `globalThis.fetch.preconnect` on the lease base URL. That opens an idle, zero-byte TCP connection. The single-threaded capture receiver blocks reading a request line from that connection, so the conductor's real request waits in the listen backlog. The conductor owns every provider connection, so the worker must open none.

## 2) Scope and Surfaces

This change touches one danger-zone path, plus this ACR:

- `breadboard/rl/harness/runners/omp_native_tool_worker.ts`: in `initialize`, before `createAgentSession`, replaces `globalThis.fetch` for the worker's lifetime with a wrapper that forwards `(input, init)` to the original fetch and has no `preconnect` property. `fetch.preconnect` itself is non-configurable. Pinned `sdk.ts:4354` returns before opening a socket when `preconnect` is not a function, and that covers every call site (`sdk.ts:1642,2312,2636,2688`).
- `docs/contracts/policies/acr/ACR-20260924-omp-native-worker-no-preconnect.md`: records this decision.

- Danger-zone: yes.

## 3) Coupling and Generalization Impact

The change is confined to the OMP worker process. It leaves the conductor, native-stream profiles, and other targets untouched. The model binding (`baseUrl`, `api`, `compat`), the system prompt, tool schemas, and descriptions are unchanged. They come from the model and tool registries, not from connection state. `preconnectModelHost` returns void, swallows errors, and writes no session, model or settings state. Pinned fetch wrappers already tolerate a missing `preconnect` (`ai/src/utils/transport-fetch.ts:41`, `ai/src/utils/proxy.ts:209`). Any fetch the pinned SDK makes still reaches the original fetch.

## 4) Change Classification

- Classification: `additive`.

The fix is corrective. It removes a supplier side effect, an optimization socket, that the worker must not produce. No interface, schema, or request byte changes.

## 5) Evidence and Validation Plan

- Host Bun 1.3.14 with the kit capture receiver and the recorded diag-w23 request body: `.tmp/bbe4/omp-preconnect-fix-probe-output.jsonl` (SHA-256 `226975384c7002ec742e1d18097fd396534a7c1ec2072382448c6aafc30cee43`). With the worker's wrapper lines copied verbatim, `preconnect` is `undefined`, Bun holds no connection to the receiver, and the POST returns 200 in 0.008 s. The unwrapped control in the same run holds an ESTABLISHED Bun connection, and the POST times out at 5 s.
- The focused OMP test files pass on macOS. Tests that need the installed pinned runtime skip on this host, because the pinned source tree has no `node_modules`.
- The danger-zone ACR guard must accept the changed-file list, and the kernel contract-pack checker must accept its manifest. The DO-2 installed replay of the first OMP request is **PENDING**. This document asserts no installed result.

## 6) Rollout Plan

Carry the fix in the OMP lane, then confirm it with an installed DO-2 Linux x64 replay. That replay must show that the Bun worker holds no connection to the lease port and that the first request is served. Independent review of this commit and applicable CI come before any merge.

## 7) Rollback Plan

If the wrapper changes any fetch behaviour the pinned SDK depends on, revert this commit through the normal protected process. Do not restore the preconnect. Instead, give the worker a binding that opens no connection without changing request bytes.

## 8) Approvals

Independent review, applicable CI, the installed DO-2 replay, and the protected merge decision remain pending. Main and the campaign owner hold those decisions. This document grants no merge authority or external acceptance.
