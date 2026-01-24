{% raw %}## Plan Mode — Tools Only{% endraw %}

Respond ONLY with one or more tool calls using the Pythonic format:

<TOOL_CALL> read_file(path="...") </TOOL_CALL>
<TOOL_CALL> list_dir(path=".", depth=1) </TOOL_CALL>

Rules:
- Do not include any prose, explanations, or code fences.
- Use only read_file() and list_dir() in plan mode.
- Keep arguments explicit (e.g., depth=1).
- Paths must be within the workspace (no system absolute paths).

Progression:
- Limit exploration to at most two plan turns; then begin creating the initial project skeleton in build mode.
