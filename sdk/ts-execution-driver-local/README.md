# @breadboard/execution-driver-local

Trusted-local execution driver helpers.

Current scope:
- classify trusted local and local-process placements
- build sandbox requests for local-process execution only when the capability model allows it
- participate in planned-execution records with explicit side-effect and evidence expectations
- provide a real local-process execution helper for trusted development lanes
- provide a trusted-local persistent terminal-session manager for process-backed session lanes

Non-goal:
- claiming sandbox-grade security

The terminal-session helper is intentionally narrow:

- process-backed
- pipes-based, not PTY-backed
- in-memory registry
- intended for trusted development and conformance work, not hostile-code isolation
