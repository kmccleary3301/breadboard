# Support Ladder

| Level | Meaning |
| --- | --- |
| Local schema | EnvPackage and record schemas validate. |
| Local lifecycle | Toy reset/step/evaluate/snapshot/restore/terminate works. |
| Local replay/export | Deterministic replay, projection, and JSONL/Parquet probe artifacts work. |
| Controlled SWE toy | 10-task controlled SWE-shaped run with hardening and QC. |
| Fixture adapter probe | Preserved/lost-field report exists with source artifacts, field mappings, fidelity notes, promotion requirements, and no production support. |
| Production support | Requires live external adapter execution, preserved/lost-field closure, docs, and regression tests. Not reached in Phase 1 so far. |
| MI300X validation | Requires M12 target-node run. Still blocked. |

Current highest support level: local controlled SWE toy + fixture/jsonl adapter probes, with JSONL/Parquet export probe artifacts.
