# Compaction differential lane support

This package owns recorded mock provider transports, stock process lifecycles,
scenario construction, native evidence guards and request-byte comparison.
`scripts/compaction_lanes` contains only command entrypoints.

OpenCode responsibilities are recording, MCP relay, scenarios, stock process
and API evidence, BreadBoard episode execution, guarded comparison and lane
orchestration. Pi separates target loading, scenarios, stock RPC settlement,
BreadBoard episodes, guarded request comparison and orchestration.

Acceptance compares raw request bodies first. Reports retain every differing
or missing request index and raw SHA-256 digest. Named deviations record their
source authority and exact edited JSON positions and values. Guards may change
only their declared fields; untouched wire member order, whitespace and escaping
remain strict. Parsed equality alone never admits a representation difference.
Dates, working directories and package paths have no implicit normalization.

The OpenCode native-created-time oracle selector remains independent of wire
comparison, and strict success still requires no unresolved request differences.
Existing pinned target documents and stock-owned runtime behavior are unchanged.
