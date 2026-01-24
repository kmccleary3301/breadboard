# C‑Trees Experiments

This folder defines **offline** experiment scenarios and policy presets for C‑Trees.

## Quickstart

Run the offline harness:

```bash
python scripts/ctlab_experiments.py
```

Results are written to `experiments/ctrees/results.json`.

## Add a scenario

1) Create a JSONL eventlog in `experiments/ctrees/scenarios/`.
   - First line should be a header:
     ```json
     {"_type":"ctree_eventlog_header","schema_version":"0.1"}
     ```
   - Each subsequent line should be a C‑Tree event:
     ```json
     {"kind":"message","payload":{"role":"user","content":"Hello"},"turn":1}
     ```

2) Register it in `experiments/ctrees/registry.json`:
   ```json
   {
     "id": "my_scenario",
     "path": "experiments/ctrees/scenarios/my_scenario.jsonl",
     "description": "Short description"
   }
   ```

## Add a policy preset

In `experiments/ctrees/registry.json`, add a policy entry:

```json
{
  "id": "my_policy",
  "selection_config": {"max_nodes": 2},
  "header_config": {"mode": "hash_only", "preview_chars": 16, "redact_secret_like": true},
  "collapse_target": 2,
  "collapse_mode": "all_but_last",
  "stage": "FROZEN"
}
```

## Notes

- Event logs should be deterministic (no timestamps/seq fields).
- Use stable task IDs or leave them out; the harness uses C‑Trees aliasing for stability.
