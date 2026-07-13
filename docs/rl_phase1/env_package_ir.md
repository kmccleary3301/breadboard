# EnvPackage IR v1alpha

EnvPackage v1alpha defines a runnable environment package with provenance, tasksets, splits, harness contract, runtime envelope, verifier, reward, renderer, hardening policy, replay requirements, and export eligibility.

Golden packages:

| Package | Purpose |
| --- | --- |
| `examples/rl_env_packages/python_console_toy/env_package.yaml` | Trusted local process toy lifecycle proof. |
| `examples/rl_env_packages/swe_toy_patch/env_package.yaml` | Controlled SWE-shaped package with 10 visible task ids and protected split guard. |

Validation command:

```bash
python -m pytest tests/rl/env_package -q
```

Core rule: runnable, exportable, and trainable are separate states. A package can be runnable but not trainable because of license, contamination, replay, hardening, or token-fidelity gates.
