# Manual QC Guide

Review accepted, rejected, and quarantined rows before promoting claims.

Minimum review dimensions:

| Dimension | Check |
| --- | --- |
| Trace completeness | Reset, step, evaluate, evidence, projection are present. |
| Verifier evidence | Evidence hash exists and rerun agreement is recorded where available. |
| Hardening status | Quarantine findings are not silently accepted. |
| Replay status | Replay mismatch blocks export. |
| Token/mask validity | M7 rows pass length and logprob gates. |
| Projection manifest | Preserved and lost fields are explicit. |
| Claim wording | Source and support level are named exactly. |

M6 QC artifact:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/qc_report.json
```
