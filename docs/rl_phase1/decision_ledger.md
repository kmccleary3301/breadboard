# RL Phase 1 Decision Ledger

| Decision | Status | Rationale |
| --- | --- | --- |
| Keep BreadBoard graph/replay/runtime canonical. | Locked | Trainer rows are projections and cannot replace evidence truth. |
| Use `breadboard/rl/` for new substrate. | Locked | Preserves existing `agentic_coder_prototype/rl` overlay. |
| Use controlled SWE toy before external SWE source. | Current M6 source | Avoids external setup blocking substrate proof. |
| Use JSONL VeRL-shaped probe before DataProto. | Current M7 scope | Proves row contract and smoke consumer first. |
| Treat adapter reports as probes. | Current M9 scope | Preserved/lost-field reports are not production integrations. |
| Backload MI300X validation. | Locked | M12 requires target hardware preflight and run evidence. |
