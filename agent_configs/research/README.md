# Research configurations

These profiles are comparator and experimental arms, not one of the six current upstream-harness catalog entries.

- `atp_hilbert_like_gpt54_v1.yaml`: standalone BreadBoard proof-edit/verify/repair comparator used by frozen runtime-record evidence.
- `atp_hilbert_like_gpt54_v2.yaml`: no-tool Hilbert-like formal-task profile.
- `atp_hilbert_shell_gpt54_v1.yaml`: shell-only Hilbert-like formal-task profile.

Use the formal-pack runner with the current research path:

```bash
python scripts/run_bb_formal_pack_v1.py \
  --config agent_configs/research/atp_hilbert_like_gpt54_v2.yaml
```

These files make no parity claim against Hilbert internals.
