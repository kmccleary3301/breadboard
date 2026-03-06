# E4 Target Packages

Each directory under `config/e4_targets/` is a tracked, version-pinned harness package.

These packages serve two purposes:

1. They are machine-loadable config bases for the latest E4 replay/live lanes.
2. They are human-readable dossiers for the target harness version, with local copies
   of prompts, references, and policy material that would otherwise be hidden behind
   gitignored refs or opaque runtime code.

The intended shape is:

- `config/e4_targets/<harness>/<version>/harness*.yaml` for the canonical package config
- `config/e4_targets/<harness>/<version>/prompts/` for directly inspectable prompt assets
- `config/e4_targets/<harness>/<version>/refs/` for nearby source/reference material
- versioned `agent_configs/*__<snapshot>.yaml` files that extend these package configs

A short replay overlay is acceptable only when the package itself exposes the harness
substrate clearly.
