# SWE Hardening

M5 validates local hardening primitives and reward-hack probe fixtures. M6 validates a 10-task controlled SWE toy run.

Controls covered:

| Control | Evidence |
| --- | --- |
| Python import-hook detection | `tests/rl/security/test_python_import_hook_cleanup.py` |
| Symlink escape detection | `tests/rl/security/test_symlink_escape.py` |
| Process cleanup before verify | `tests/rl/security/test_process_cleanup_contract.py` |
| Verifier evidence hashes | `tests/rl/security/test_verifier_evidence_hashes.py` |
| Quarantine-first behavior | `tests/rl/security/test_quarantine_rules.py` |
| Adversarial fixtures | `tests/rl/security/test_reward_hack_probe_suite.py` |

M6 run artifacts:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/
```

Claim boundary: controlled SWE toy slice only. This is not SWE-Gym, SWE-rebench-V2, SETA, or Toolathlon support.
