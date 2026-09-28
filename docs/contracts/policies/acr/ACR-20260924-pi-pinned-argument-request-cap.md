# ACR-20260924-pi-pinned-argument-request-cap

- `acr_id`: `ACR-20260924-pi-pinned-argument-request-cap`
- `title`: Pinned Pi argument parsing and request-cap termination parity
- `author`: BreadBoard E4 Pi replay lane
- `date`: 2026-09-24
- `status`: implemented; protected merge and DO-2 installed replay pending

## 1) Problem Statement

BreadBoard's Pi 0.73.1 response path repaired partial tool arguments in Python instead of calling Pi's pinned `parseStreamingJson`. It replaced non-object arguments with `{}` before the pinned validator could report the tool error. The request-cap replay also needed to distinguish a supplier capture's explicit request-limit stop from a generic error without treating an arbitrary `error` as a matching cause. Running the pinned parser synchronously blocked the event loop, including the episode deadline.

## 2) Scope and Surfaces

The diff from GitHub main `ba63c4b3` to Pi head `c2ccfeb4` changes these six danger-zone paths:

- `breadboard/rl/harness/native_stream_consumers.py`: awaits Pi response preparation.
- `breadboard/rl/harness/native_stream_profiles.py`: requires model ID and provider from the native-stream bootstrap, and passes them with the API variant into Pi state.
- `breadboard/rl/harness/pi_native_tools.py`: sends lone surrogates as ASCII JSON escapes, adds a cancellable asynchronous pinned-worker call and batched argument parsing, reports launch errors as `PiNativeWorkerError`, and passes tool arguments without substituting `{}`.
- `breadboard/rl/harness/pi_tools_0_73_1.mjs`: delegates partial arguments to pinned `parseStreamingJson`, leaves tool-argument type errors to pinned validation, returns per-call tool errors, and retains sampled arguments in assistant history separately from converted execution arguments.
- `breadboard/rl/harness/runners/conductor.py`: awaits response preparation when it returns an awaitable, before committing the assistant message.
- `breadboard/rl/harness/runners/pi_semantics.py`: removes the Python JSON-repair parser, batches pinned parsing before state mutation, records model/provider/API in assistant messages, and shapes the request-cap terminal assistant message.

The other changed paths are `conformance/comparators/pi_coding_agent_0_73_1.py`, which permits a request-limit cause normalization only under the declared scenario cap and role-specific terminal evidence, plus four regression files: `tests/e4_parity/test_pi_0_73_1_comparator.py`, `tests/rl/harness/test_pi_0_73_1_semantics.py`, `tests/rl/harness/test_pi_native_stream_conductor.py`, and `tests/rl/harness/test_runner_conductor.py`. Kernel danger-zone change? yes.

## 3) Coupling and Generalization Impact

Pi-specific parsing remains in the Pi profile and its pinned Node worker; the Conductor change handles any awaitable response preparation without a Pi branch. One worker parses a response's arguments as a batch. Cancellation kills and reaps that worker before the Pi state commits the response. The supplier and replay comparator both require the scenario's request cap, matching request and stream-attempt counts, an empty-text assistant error stop, and the appropriate role-specific cause; native stop reasons remain unnormalized. Other error stops cannot acquire the `request_limit` cause. The pinned worker owns argument repair, validation, and sampled-history shape instead of a second Python parser.

## 4) Change Classification

- Classification: `breaking`.

The internal Pi state constructor now requires model ID and provider, and Pi response preparation and its native-stream consumer are asynchronous. Their callers in this diff change together. No persistence schema or public plan file changes in this PR delta.

## 5) Evidence and Validation Plan

- Independent exact-head reviewer `e4-sol-review-w3-2` ACCEPTed `c2ccfeb48e6f870b139e50bbe6bfaed40455d8f0` with no findings. Its 33,079-input parser differential recorded zero mismatches in `/tmp/e4-sol-review-w3-2-c2ccfeb4-differential.json` (SHA-256 `a8787474aa22a95d03bf4643b9106980020cfcecdaa33c9600d91cd11f9a4b26`). Its stalled-worker cancellation and reap result is `/tmp/e4-sol-review-w3-2-c2ccfeb4-blocking-worker.json` (SHA-256 `52dd0f7185a302184462c9c5d6014eb2fb8dac8447d256bd8a6b5363d53a1555`); the review also recorded a cancelled parse without a committed response and six supplier-versus-replay non-object tool exchanges.
- The campaign tracker records 291 focused tests passed at `c2ccfeb4`. The PR comparator tests exercise declared-cap and counterfeit request-limit evidence. The danger-zone ACR guard must pass on the changed-file list from `ba63c4b3..HEAD`.
- No Pi local-vm-linux run is asserted here. A local VM run would not establish DO-2 qualification. DO-2 installed Pi replay is PENDING; this document does not claim an installed replay or external acceptance.

## 6) Rollout Plan

Require this changed ACR, the relevant installed-pinned-Pi CI lane, independent review of this tail commit, and the applicable protected checks before Main considers merging PR #135. Do not treat a local Node test, a skipped missing-dependency test, or this ACR as an installed DO-2 replay.

## 7) Rollback Plan

If parsing diverges from the pinned Pi output, a deadline fails to cancel the worker, or a generic error matches the request-limit cause, revert the Pi change through the protected review path. Do not restore the Python parser as a silent fallback or erase the supplier packet and review artifacts. Rerun the focused Pi checks and the danger-zone ACR guard on the rollback candidate.

## 8) Approvals

Independent exact-head Pi review `e4-sol-review-w3-2` ACCEPTed `c2ccfeb4`. Independent tail review of this ACR commit, applicable CI, and the protected merge decision remain pending. Main and the campaign owner retain those decisions; this document grants no merge authority or DO-2 qualification.
