# RLM Mode Quickstart (Preview)

RLM mode is an opt-in extension for long-context recursive querying.

Status:
- additive preview path,
- disabled by default,
- parity profiles remain unchanged unless explicitly enabled.

## Enable

Use:
- `agent_configs/rlm_base_v1.yaml`

or set:
- `features.rlm.enabled: true`

## New tools (when enabled)

1. `blob.put`
2. `blob.put_file_slice`
3. `blob.get`
4. `blob.search`
5. `llm.query`
6. `llm.batch_query`

`llm.batch_query` is optional and disabled unless:
- `features.rlm.scheduling.mode: batch` or
- `features.rlm.scheduling.batch.enabled: true`

## Safety and parity fences

1. RLM tools are hidden when `features.rlm.enabled` is false.
2. Existing parity harness configs should keep RLM disabled.
3. Hard budgets should always be configured:
   - depth,
   - subcall count,
   - tokens,
   - cost,
   - wall-clock.

## Artifacts

When enabled, runs write:
1. `meta/rlm_subcalls.jsonl`
2. `meta/rlm_blobs_manifest.jsonl`
3. `meta/rlm_branches.json`
4. `meta/rlm_hybrid_events.jsonl`
5. `meta/rlm_router_decisions.jsonl`
6. `meta/ctrees/rlm_projection.jsonl` (optional projection hook stream)

Workspace mirror copies are also written under `.breadboard/meta/`.

## Minimal usage pattern

1. Create blob from file slice:
```text
blob.put_file_slice(path="src/main.py", start_line=1, end_line=200)
```
2. Search for anchor snippets:
```text
blob.search(blob_ids=["sha256:..."], query="TODO", max_results=10)
```
3. Query with blob context:
```text
llm.query(prompt="Summarize unresolved TODOs", blob_refs=["sha256:..."])
```

4. Batch query with deterministic output ordering:
```text
llm.batch_query(
  queries=[
    {"prompt":"Summarize TODOs in parser.py","branch_id":"analysis.parser"},
    {"prompt":"Summarize TODOs in router.py","branch_id":"analysis.router"}
  ],
  branch_id="analysis.root"
)
```

Optional behavior controls:
- `features.rlm.scheduling.batch.max_concurrency`
- `features.rlm.scheduling.batch.max_concurrency_per_branch`
- `features.rlm.scheduling.batch.retries`
- `features.rlm.scheduling.batch.timeout_seconds`
- `features.rlm.scheduling.batch.fail_fast`
- per-call override: `fail_fast: true|false`

## Notes

This tranche includes:
- sync `llm.query`,
- deterministic `llm.batch_query` ordering (even under parallel completion),
- retries/timeouts/fail-fast policy controls,
- replay-friendly artifact outputs.
