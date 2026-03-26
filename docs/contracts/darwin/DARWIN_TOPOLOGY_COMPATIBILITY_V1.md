# DARWIN Topology Compatibility V1

This document freezes which topology families are valid per lane during the current DARWIN Phase-1 tranche.

## Active lanes

- `lane.atp`: `single`, `pev`, `pwrv`
- `lane.harness`: `single`, `pev`
- `lane.systems`: `single`, `pev`, `pwrv`
- `lane.repo_swe`: `single`, `pev`, `pwrv`

## Scheduling lane

- `lane.scheduling`: `single`, `pev`; `pwrv` deferred
- current evaluator slice is valid only when the scenario pack and exact checker are unchanged

## Research lane

- `lane.research`: `single`, `pev`; `pwrv` deferred
- the evaluator slice is valid only when the research pack and citation scorer are unchanged

## Rule

No topology comparison is valid unless:

1. the lane/topology pair is declared compatible
2. the required policy bundle exists
3. budget class is comparable
4. evaluator slice is identical
