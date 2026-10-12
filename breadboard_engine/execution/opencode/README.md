# Pinned OpenCode stock execution

`session.py` owns root request admission, recovery and compaction coordination.
`lifecycle.py` owns the native subprocess, durable binding and SSE readers.
`interception.py` classifies labelled requests; `wire.py` projects Responses bytes.
`driver.py` connects the session to conductor admissions and provider credentials.
Both OpenCode and Claude use `execution/local_http_proxy.py` for listener/request
ownership and `execution/opencode_auxiliary.py` for cancellable upstream forwarding.

Canonical shared tool definitions live in `implementations/tools/defs_opencode_shared`.
`compilation/tool_catalogs.py` expands the unchanged pinned OpenCode/OMO catalog paths
for static compilation, dynamic tools and registry consumers, preserving filename order.
OMO retains its own glob, grep, skill and task definitions.

The six `tests/compaction/test_opencode_stock_*` domains retain independent stock
oracles. Missing native dependencies skip only affected stock fixtures. Default
fixtures disable remote MCP discovery. Set `OPENCODE_TEST_LIVE_MCP=1` to admit
live Exa, grep.app and Context7 conformance (with pinned stock dependencies installed).
