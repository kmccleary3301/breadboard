# VeRL-Shaped Export Contract

M7 emits `bb.verl_probe_row.v1alpha` JSONL and Parquet rows from the M6 controlled SWE run.

Artifact:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.jsonl
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.parquet
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/projection_manifest.json
```

Smoke report:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/smoke_consumer_report.json
```

Required row groups:

| Group | Fields |
| --- | --- |
| Identity | rollout, trajectory, episode, task, split, package, group ids. |
| Policy | policy id/version, checkpoint ref, model requested/served, provider, engine, sampling config, policy staleness. |
| Tokens | prompt ids, completion ids, input ids, masks, completion logprobs when trainable candidate, explicit logprob availability/unavailability status. |
| Renderer | renderer id/version/config hash, tokenizer hash, chat template hash, stop ids, fidelity class. |
| Reward/runtime | reward scalar/vector, verifier id/version/hash, evidence refs, runtime backend/signature, image digest, state refs, artifact refs, package hash, metrics. |
| Admission | row status, hardening status, replay status, quarantine status, trainable flag, eligible exports, blocked reasons. |
| Projection | row projection ids plus export-level manifest preserving BreadBoard graph/replay/runtime as canonical truth. |

Claim boundary: JSONL/Parquet probe only. This is not DataProto, not trainer execution, and not GRPO/PPO readiness.
