# Runtime capture and replay for E4

## Scope

Runtime emission is available behind `BREADBOARD_EMIT_PRIMITIVES=1`. When the flag is set, the CLI bridge writes session-start records and hot-path runtime records under `artifacts/runtime_records/<session_id>/`. Test and capture runs may redirect that root with `BREADBOARD_RUNTIME_RECORD_ROOT`.

The session-start set covers facts the bridge already has in memory:

- `bb.effective_config_graph.v1`
- `bb.capability_registry.v1`
- `bb.effective_tool_surface.v1`
- `bb.work_item.v1`

The hot path adds:

- `bb.kernel_event.v2`
- `bb.tool_call.v2`
- `bb.tool_execution_outcome.v2`
- `bb.tool_model_render.v2`
- `bb.session_transcript.v2`

## Current path

1. The session service accepts `POST /sessions`.
2. If `BREADBOARD_EMIT_PRIMITIVES=1`, it compiles the loaded agent config and request metadata through the primitive boundary, then passes the session runtime record directory into the runner.
3. The runner defaults enabled primitive emission to strict mode unless `BREADBOARD_PRIMITIVES_MODE` is set to `strict`, `quarantine`, or `off`.
4. `SessionState` dual-emits kernel events without changing the legacy in-memory event flow.
5. Tool declaration, execution, terminal outcome, and model-render records are written with a shared `call_id`.
6. `/v1/e4/records?source=runtime&schema_version=<schema>` and `/v1/sessions/{session_id}/records` read runtime record streams.
7. `BREADBOARD_CONFIG_PLANE_DIALECT` selects only the session-start config-plane coordination records: `v2` writes `bb.coordination_slice.v2`, `v3` writes `bb.coordination_pack.v3`, and `both` writes both. It defaults to `v2` and does not select hot-path kernel, tool, terminal, model-render, or transcript record versions.

The flag-off path writes no runtime records and keeps existing session behavior unchanged. Production remains flag-off by default. Dev/CI profiles that enable runtime emission use strict mode by default.

## WS-C soak evidence

`scripts/runtime_emission_soak.py` runs the local deterministic WS-C soak without provider credentials. It uses the same 25-session workload with runtime emission strict-on and flag-off, includes native and text-dialect tool calls, multi-turn sessions, tool failures/denials, compaction-boundary transcript snapshots, and provider-faithful deterministic latency. The report at `../docs_tmp/phase_17/runtime_soak/runtime_emission_soak_report.json` recorded 25 sessions, `quarantine_count: 0`, strict wall-clock `15.711208833003184s`, flag-off wall-clock `15.258226167003158s`, and `overhead_percent: 2.9687767178312505`.

Because that soak passed the C10 thresholds, dev/CI capture profiles that enable `BREADBOARD_EMIT_PRIMITIVES=1` use strict primitive mode by default when `BREADBOARD_PRIMITIVES_MODE` is unset or invalid. Production remains flag-off by default; setting `BREADBOARD_PRIMITIVES_MODE=off` is still an explicit escape hatch for local diagnostics.

## Replay requirements before WS-J self-capture

Runtime records can become C4 support only after the runtime path preserves the same chain as the standalone builders:

- a frozen target/config row;
- raw capture with exact command, target version, run id, provider model, and sandbox mode;
- deterministic replay input;
- comparator output with named assertions;
- a v2 support claim whose asserted behaviors resolve to passed comparator assertion ids;
- an evidence manifest with hash refs for every governed artifact;
- a node-gate validation report from `validate_e4_c4_chain.py`.

The runtime path must not skip the comparator or replace artifact hashes with in-memory trust. If the session service emits a record that cannot be replayed, the coverage row stays below `c4_accepted`.

## Non-goals

- No provider, sandbox, or target-family parity claim from runtime emission alone.
- No broad claim that all future session state is captured.
- No production default-on emission until storage, retention, and redaction policies are reviewed.
- C3 evidence status remains unchanged until WS-J records a full BreadBoard self-capture lane.

## Open migration work

- Add a replay fixture format that can drive the same comparator protocol used by accepted E4 lanes.
- Record redaction decisions for host-only and secret-derived config values.
- Decide which runtime records belong in long-term evidence storage versus local diagnostics.
- Add C4 lane rows only after a full freeze/capture/replay/comparator/support-claim chain exists.
