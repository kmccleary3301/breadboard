# RLM Phase-2 Migration Notes

Scope: additive RLM capability integration while preserving default parity behavior.

## 1) Compatibility

1. RLM remains opt-in:
- enabled only via `features.rlm.enabled: true`.

2. Parity/E4 profiles remain unaffected by default:
- long-running and RLM surfaces are not implicitly enabled in parity configs.

3. Existing harness-emulation profiles retain their baseline behavior unless explicitly changed.

## 2) New/extended config knobs

Under `features.rlm.scheduling.batch`:
- `max_concurrency`
- `max_concurrency_per_branch`
- `retries`
- `timeout_seconds`
- `fail_fast`

## 3) Tool surfaces

Added RLM tool definitions:
- `blob.put`
- `blob.put_file_slice`
- `blob.get`
- `blob.search`
- `llm.query`
- `llm.batch_query`

## 4) Artifact surfaces

RLM-enabled runs may produce:
- `meta/rlm_subcalls.jsonl`
- `meta/rlm_batch_subcalls.jsonl`
- `meta/rlm_batch_summary.json`
- `meta/rlm_blobs_manifest.jsonl`
- `meta/rlm_branches.json`
- `meta/rlm_hybrid_events.jsonl`

## 5) Operational changes

1. Live eval runs should use both:
- spend cap
- token cap

2. Heavier scenarios may require increased timeout budgets.

3. Artifact writers now enforce JSON-safe serialization for usage metadata to avoid silent drops of subcall logs.

## 6) Recommended adoption path

1. Start with `agent_configs/rlm_base_v1.yaml` in non-parity lanes.
2. Validate with `scripts/validate_rlm_artifacts.py`.
3. Run parity audit before any release candidate:
- `scripts/audit_longrun_parity_disabled.py`

## 7) Rollback

Immediate rollback path:
1. Disable RLM in active profile.
2. Revert to parity/default profile without RLM.
3. Re-run parity audit and key replay tests.
